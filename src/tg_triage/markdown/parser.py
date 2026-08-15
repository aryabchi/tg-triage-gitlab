"""Parse an uploaded Markdown contract into an in-memory plan.

``## Legend`` is skipped. Unknown ``##`` headings fail the whole file. Item
outcome comes from the section, not from a per-item Action field. Deleted
``###`` blocks are simply absent from the plan.
"""

from __future__ import annotations

from datetime import date

from tg_triage.domain import IssueRef, MarkdownPlan, RepositoryId, TriageItem, TriageOutcome


class PlanValidationError(ValueError):
    """The contract cannot be parsed or executed as written."""


_SECTION_OUTCOMES = {
    "create": TriageOutcome.CREATE_NEW,
    "skip": TriageOutcome.LINK_EXISTING,
    "uncertain": TriageOutcome.UNCERTAIN,
}


def parse(text: str) -> MarkdownPlan:
    """Turn contract text into items tagged by section.

    Does not check required fields or the configured repo list; call
    :func:`validate_plan` for that.

    Raises:
        PlanValidationError: If an unknown ``##`` section appears.
    """
    lines = text.splitlines()
    run_id, since_date, start = _parse_preamble(lines)
    items: list[TriageItem] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line[3:].strip()
            key = heading.lower()
            if key == "legend":
                i = _skip_until_section(lines, i + 1)
                continue
            if key not in _SECTION_OUTCOMES:
                raise PlanValidationError(f"unknown section: {heading}")
            i += 1
            section_items, i = _parse_section_items(lines, i, _SECTION_OUTCOMES[key])
            items.extend(section_items)
            continue
        i += 1
    return MarkdownPlan(items=tuple(items), run_id=run_id, since_date=since_date)


def _parse_preamble(lines: list[str]) -> tuple[int | None, date | None, int]:
    """Read ``# run:`` and ``# since:`` before the first ``##`` heading."""
    run_id: int | None = None
    since_date: date | None = None
    index = 0
    while index < len(lines) and not lines[index].startswith("## "):
        stripped = lines[index].strip()
        if stripped.startswith("# run:"):
            run_id = int(stripped.removeprefix("# run:").strip())
        elif stripped.startswith("# since:"):
            since_date = date.fromisoformat(stripped.removeprefix("# since:").strip())
        index += 1
    return run_id, since_date, index


def _skip_until_section(lines: list[str], index: int) -> int:
    """Advance to the next ``##`` heading, or to the end of the file."""
    while index < len(lines) and not lines[index].startswith("## "):
        index += 1
    return index


def _parse_section_items(
    lines: list[str],
    index: int,
    outcome: TriageOutcome,
) -> tuple[list[TriageItem], int]:
    """Parse ``###`` blocks until the next ``##`` heading."""
    items: list[TriageItem] = []
    while index < len(lines) and not lines[index].startswith("## "):
        if lines[index].startswith("### "):
            item, index = _parse_item(lines, index, outcome)
            items.append(item)
            continue
        index += 1
    return items, index


def _parse_item(
    lines: list[str],
    index: int,
    outcome: TriageOutcome,
) -> tuple[TriageItem, int]:
    """Parse one ``###`` block into a TriageItem."""
    title = lines[index][4:].strip()
    index += 1
    fields: dict[str, str] = {}
    while index < len(lines):
        line = lines[index]
        if line.startswith("## ") or line.startswith("### "):
            break
        field = _field_name_and_value(line)
        if field is None:
            index += 1
            continue
        name, value = field
        if name == "body" and value == "|":
            body, index = _read_pipe_block(lines, index + 1)
            fields["body"] = body
            continue
        fields[name] = value
        index += 1
    return _item_from_fields(title, outcome, fields), index


def _field_name_and_value(line: str) -> tuple[str, str] | None:
    """Return a list-field name and value, or ``None`` if the line is not a field."""
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    name, sep, value = stripped[2:].partition(":")
    if not sep:
        return None
    return name.strip().lower(), value.strip()


def _read_pipe_block(lines: list[str], index: int) -> tuple[str, int]:
    """Read indented lines after ``Body: |`` and strip the common indent."""
    collected: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.startswith("## ") or line.startswith("### "):
            break
        if line.strip() == "":
            if collected:
                collected.append("")
            index += 1
            continue
        if line.startswith(" ") or line.startswith("\t"):
            collected.append(line)
            index += 1
            continue
        break
    while collected and collected[-1] == "":
        collected.pop()
    if not collected:
        return "", index
    indents = [len(row) - len(row.lstrip(" ")) for row in collected if row.strip()]
    indent = min(indents) if indents else 0
    body = "\n".join(row[indent:] if len(row) >= indent else row for row in collected)
    return body, index


def _item_from_fields(
    title: str,
    outcome: TriageOutcome,
    fields: dict[str, str],
) -> TriageItem:
    """Map parsed field names onto a TriageItem. Missing fields stay empty."""
    repo_raw = fields.get("repo")
    repository: RepositoryId | None = None
    if repo_raw and repo_raw.lower() != "unknown":
        repository = RepositoryId.parse(repo_raw)
    existing_raw = fields.get("existing")
    return TriageItem(
        problem_ids=_parse_problems(fields.get("problems", "")),
        outcome=outcome,
        title=title,
        body=fields.get("body", ""),
        rationale=fields.get("reason", ""),
        repository=repository,
        existing_issue=IssueRef.parse(existing_raw) if existing_raw else None,
        priority=fields.get("priority") or None,
    )


def _parse_problems(raw: str) -> tuple[int, ...]:
    """Parse ``#101, #102`` or ``101, 102`` into problem ids."""
    if not raw.strip():
        return ()
    ids: list[int] = []
    for part in raw.split(","):
        token = part.strip().lstrip("#")
        if token:
            ids.append(int(token))
    return tuple(ids)
