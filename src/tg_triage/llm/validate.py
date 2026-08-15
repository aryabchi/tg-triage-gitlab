"""Validate cluster and match JSON before Markdown is rendered.

The model is not trusted: schema, coverage, and identity checks live here.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from typing import cast

from tg_triage.domain import (
    CoverageError,
    EvidencePack,
    IssueRef,
    RepositoryId,
    SnippetKind,
    TriageItem,
    TriageOutcome,
    assert_coverage,
)
from tg_triage.ports.llm import ClusterItem

_OUTCOMES = {
    "create_new": TriageOutcome.CREATE_NEW,
    "link_existing": TriageOutcome.LINK_EXISTING,
    "uncertain": TriageOutcome.UNCERTAIN,
}
_PRIORITIES = frozenset({"P1", "P2", "P3"})


class LlmValidationError(ValueError):
    """Model output is not valid cluster or match JSON."""


def parse_json(text: str) -> object:
    """Parse a JSON document.

    Raises:
        LlmValidationError: If ``text`` is not JSON.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmValidationError("response is not JSON") from exc


def validate_cluster(data: object, problem_ids: Iterable[int]) -> tuple[ClusterItem, ...]:
    """Require a JSON object whose items partition ``problem_ids``.

    Raises:
        LlmValidationError: On schema mismatch.
        CoverageError: If ids are missing, extra, or duplicated.
    """
    items_raw = _require_items(data)
    items: list[ClusterItem] = []
    for raw in items_raw:
        mapping = _require_object(raw, "cluster item")
        summary = mapping.get("summary")
        if not isinstance(summary, str):
            raise LlmValidationError("cluster item summary must be a string")
        items.append(
            ClusterItem(summary=summary, problem_ids=_require_problem_ids(mapping))
        )
    clustered = tuple(items)
    try:
        assert_coverage(problem_ids, clustered)
    except CoverageError as exc:
        raise LlmValidationError(str(exc)) from exc
    return clustered


def validate_match(
    data: object,
    *,
    allowed_repos: Collection[RepositoryId],
    pack: EvidencePack,
    problem_ids: Iterable[int],
) -> tuple[TriageItem, ...]:
    """Map match JSON to TriageItems. Invented repos and unknown issue numbers fail.

    Uncertain may use ``unknown`` or a configured repository (Markdown allows a
    repo hint). Create requires a configured repo, title, and body. Skip requires
    ``existing`` present in the snapshot. A missing ``existing`` key is treated
    as null so a schema omission does not consume the JSON retry.

    Raises:
        LlmValidationError: On schema or identity failure.
        CoverageError: If problem ids are not a partition.
    """
    allowed = {str(repo) for repo in allowed_repos}
    snapshot = _snapshot_issues(pack)
    items_raw = _require_items(data)
    items: list[TriageItem] = []
    for raw in items_raw:
        items.append(_match_item(raw, allowed, snapshot))
    matched = tuple(items)
    try:
        assert_coverage(problem_ids, matched)
    except CoverageError as exc:
        raise LlmValidationError(str(exc)) from exc
    return matched


def _require_items(data: object) -> list[object]:
    """Return the ``items`` array from a top-level JSON object."""
    if not isinstance(data, dict):
        raise LlmValidationError("JSON root must be an object")
    mapping = cast(Mapping[str, object], data)
    items = mapping.get("items")
    if not isinstance(items, list):
        raise LlmValidationError("items must be a list")
    return cast(list[object], items)


def _require_object(raw: object, label: str) -> Mapping[str, object]:
    """Require a JSON object."""
    if not isinstance(raw, dict):
        raise LlmValidationError(f"{label} must be an object")
    return cast(Mapping[str, object], raw)


def _require_problem_ids(mapping: Mapping[str, object]) -> tuple[int, ...]:
    """Read a non-empty list of integer problem ids."""
    raw = mapping.get("problem_ids")
    if not isinstance(raw, list) or not raw:
        raise LlmValidationError("problem_ids must be a non-empty list")
    ids: list[int] = []
    for value in raw:
        if not isinstance(value, int) or isinstance(value, bool):
            raise LlmValidationError("problem_ids must be integers")
        ids.append(value)
    return tuple(ids)


def _match_item(
    raw: object,
    allowed: set[str],
    snapshot: set[tuple[str, int]],
) -> TriageItem:
    """Validate one match item and map it to a TriageItem.

    ``existing`` may be omitted; that is the same as JSON null. Uncertain does
    not keep an ``existing`` value even if the model sent one.
    """
    mapping = _require_object(raw, "match item")
    required = (
        "problem_ids",
        "outcome",
        "repository",
        "priority",
        "title",
        "body",
        "rationale",
    )
    missing = [key for key in required if key not in mapping]
    if missing:
        raise LlmValidationError(f"match item missing {missing}")
    outcome_raw = mapping.get("outcome")
    if outcome_raw not in _OUTCOMES:
        raise LlmValidationError(f"invalid outcome: {outcome_raw!r}")
    outcome = _OUTCOMES[str(outcome_raw)]
    repository = _parse_repository(mapping.get("repository"), allowed)
    existing = _parse_existing(mapping.get("existing"))
    priority_raw = mapping.get("priority")
    if priority_raw is not None and priority_raw not in _PRIORITIES:
        raise LlmValidationError(f"invalid priority: {priority_raw!r}")
    title = mapping.get("title")
    body = mapping.get("body")
    rationale = mapping.get("rationale")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(rationale, str):
        raise LlmValidationError("title, body, and rationale must be strings")
    if outcome == TriageOutcome.CREATE_NEW:
        if repository is None:
            raise LlmValidationError("create_new requires a known repository")
        if not title.strip() or not body.strip():
            raise LlmValidationError("create_new requires title and body")
        existing = None
    elif outcome == TriageOutcome.LINK_EXISTING:
        if existing is None:
            raise LlmValidationError("link_existing requires existing")
        if repository is None:
            raise LlmValidationError("link_existing requires a known repository")
        if str(existing.repository) != str(repository):
            raise LlmValidationError("existing repository does not match repository")
        if (str(existing.repository), existing.number) not in snapshot:
            raise LlmValidationError(
                f"existing issue not in snapshot: {existing}"
            )
    else:
        existing = None
    return TriageItem(
        problem_ids=_require_problem_ids(mapping),
        outcome=outcome,
        title=title,
        body=body,
        rationale=rationale,
        repository=repository,
        existing_issue=existing,
        priority=str(priority_raw) if priority_raw is not None else None,
    )


def _parse_repository(raw: object, allowed: set[str]) -> RepositoryId | None:
    """Accept ``unknown`` or a configured ``owner/repo``."""
    if not isinstance(raw, str):
        raise LlmValidationError("repository must be a string")
    if raw == "unknown":
        return None
    try:
        repo = RepositoryId.parse(raw)
    except ValueError as exc:
        raise LlmValidationError(f"invalid repository: {raw}") from exc
    if str(repo) not in allowed:
        raise LlmValidationError(f"repository not in config: {repo}")
    return repo


def _parse_existing(raw: object) -> IssueRef | None:
    """Parse ``owner/repo#n`` or JSON null."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise LlmValidationError("existing must be a string or null")
    try:
        return IssueRef.parse(raw)
    except ValueError as exc:
        raise LlmValidationError(f"invalid existing: {raw}") from exc


def _snapshot_issues(pack: EvidencePack) -> set[tuple[str, int]]:
    """Issue identities present in the evidence pack."""
    found: set[tuple[str, int]] = set()
    for snippet in pack.snippets:
        if snippet.kind == SnippetKind.ISSUE and snippet.issue_number is not None:
            found.add((str(snippet.locator), snippet.issue_number))
    return found
