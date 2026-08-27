"""LLM JSON validation: coverage partition and closed-world identities."""

from __future__ import annotations

import pytest

from tg_triage.domain import (
    EvidencePack,
    EvidenceSnippet,
    RepositoryId,
    SnippetKind,
    TriageOutcome,
)
from tg_triage.llm.validate import LlmValidationError, validate_cluster, validate_match

ALLOWED = (
    RepositoryId.parse("acme/sales-dashboard"),
    RepositoryId.parse("acme/crm"),
    RepositoryId.parse("acme/customer-portal"),
)
IDS = (101, 102, 103, 104)


def _pack_with_export_issue() -> EvidencePack:
    repo = RepositoryId.parse("acme/sales-dashboard")
    return EvidencePack(
        snippets=(
            EvidenceSnippet(
                source_id="github-fixtures",
                kind=SnippetKind.ISSUE,
                locator=repo,
                title="Экспорт дашборда продаж не завершается",
                text="спиннер",
                provenance="issues.json",
                issue_number=81,
            ),
        )
    )


def test_valid_cluster_partition() -> None:
    data = {
        "items": [
            {"summary": "export", "problem_ids": [101, 102]},
            {"summary": "sync", "problem_ids": [103]},
            {"summary": "again", "problem_ids": [104]},
        ]
    }
    items = validate_cluster(data, IDS)
    assert [item.problem_ids for item in items] == [(101, 102), (103,), (104,)]


def test_cluster_missing_problem_id_fails() -> None:
    data = {"items": [{"summary": "export", "problem_ids": [101, 102]}]}
    with pytest.raises(LlmValidationError, match="missing"):
        validate_cluster(data, IDS)


def test_invented_repo_fails() -> None:
    data = {
        "items": [
            {
                "problem_ids": [101],
                "outcome": "create_new",
                "repository": "acme/secret",
                "existing": None,
                "priority": "P2",
                "title": "x",
                "body": "y",
                "rationale": "z",
            }
        ]
    }
    with pytest.raises(LlmValidationError, match="not in config"):
        validate_match(data, allowed_repos=ALLOWED, pack=_pack_with_export_issue(), problem_ids=(101,))


def test_link_existing_absent_from_pack_fails() -> None:
    data = {
        "items": [
            {
                "problem_ids": [104],
                "outcome": "link_existing",
                "repository": "acme/sales-dashboard",
                "existing": "acme/sales-dashboard#99",
                "priority": None,
                "title": "x",
                "body": "y",
                "rationale": "z",
            }
        ]
    }
    with pytest.raises(LlmValidationError, match="not in snapshot"):
        validate_match(data, allowed_repos=ALLOWED, pack=_pack_with_export_issue(), problem_ids=(104,))


def test_uncertain_unknown_repo_passes() -> None:
    data = {
        "items": [
            {
                "problem_ids": [103],
                "outcome": "uncertain",
                "repository": "unknown",
                "existing": None,
                "priority": None,
                "title": "",
                "body": "",
                "rationale": "could be crm or portal",
            }
        ]
    }
    items = validate_match(
        data,
        allowed_repos=ALLOWED,
        pack=_pack_with_export_issue(),
        problem_ids=(103,),
    )
    assert items[0].outcome == TriageOutcome.UNCERTAIN
    assert items[0].repository is None


def test_uncertain_configured_repo_passes() -> None:
    data = {
        "items": [
            {
                "problem_ids": [103],
                "outcome": "uncertain",
                "repository": "acme/crm",
                "priority": "P2",
                "title": "sync",
                "body": "",
                "rationale": "likely crm",
            }
        ]
    }
    items = validate_match(
        data,
        allowed_repos=ALLOWED,
        pack=_pack_with_export_issue(),
        problem_ids=(103,),
    )
    assert items[0].outcome == TriageOutcome.UNCERTAIN
    assert items[0].repository == RepositoryId.parse("acme/crm")
    assert items[0].existing_issue is None


def test_match_omitted_existing_is_null() -> None:
    data = {
        "items": [
            {
                "problem_ids": [101],
                "outcome": "create_new",
                "repository": "acme/sales-dashboard",
                "priority": "P1",
                "title": "export hangs",
                "body": "spinner",
                "rationale": "new",
            }
        ]
    }
    items = validate_match(
        data,
        allowed_repos=ALLOWED,
        pack=_pack_with_export_issue(),
        problem_ids=(101,),
    )
    assert items[0].outcome == TriageOutcome.CREATE_NEW
    assert items[0].existing_issue is None


def test_uncertain_invented_repo_fails() -> None:
    data = {
        "items": [
            {
                "problem_ids": [103],
                "outcome": "uncertain",
                "repository": "acme/secret",
                "existing": None,
                "priority": None,
                "title": "",
                "body": "",
                "rationale": "guess",
            }
        ]
    }
    with pytest.raises(LlmValidationError, match="not in config"):
        validate_match(
            data,
            allowed_repos=ALLOWED,
            pack=_pack_with_export_issue(),
            problem_ids=(103,),
        )

