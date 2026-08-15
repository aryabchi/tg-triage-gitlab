"""Canonical Markdown contract rendered from validated triage items.

The LLM does not write this file. Empty Create/Skip/Uncertain sections are
omitted. ``exclude`` items are not rendered (deleted blocks).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from tg_triage.domain import TriageItem, TriageOutcome

LEGEND = """\
Section is the action. Move a ### block to change outcome.
Delete a ### block to exclude it from this run (Problems stay ingested).

Create — must: Repo (owner/repo, not unknown), Problems, Body
         optional: Priority
         execute: create a GitHub issue (title + body only); Problems become linked

Skip — must: Repo, Existing (owner/repo#n), Problems
       optional: Body (plan note only; not written to GitHub), Priority
       execute: record Existing; no GitHub write; Problems become linked

Uncertain — must: Problems, Reason
            optional: Repo (may be unknown), Priority
            execute: nothing; Problems stay ingested

Suggested Priority values (optional; Markdown-only; not sent to GitHub).
Missing, unset, or non-suggested values do not fail the plan:
- P1 — user-facing broken, no workaround
- P2 — degraded, or a workaround exists
- P3 — minor, cosmetic, or nice-to-have
"""


def render(items: Sequence[TriageItem], *, run_id: int, since_date: date) -> str:
    """Build the contract file for a compile run.

    Section order is always Create, Skip, Uncertain. Item order within a
    section follows ``items``.
    """
    creates = [item for item in items if item.outcome == TriageOutcome.CREATE_NEW]
    skips = [item for item in items if item.outcome == TriageOutcome.LINK_EXISTING]
    uncertains = [item for item in items if item.outcome == TriageOutcome.UNCERTAIN]
    parts = [
        "# GitHub Issues",
        f"# run: {run_id}",
        f"# since: {since_date.isoformat()}",
        "",
        "## Legend",
        "",
        LEGEND.rstrip(),
        "",
    ]
    _append_section(parts, "Create", creates, _render_create)
    _append_section(parts, "Skip", skips, _render_skip)
    _append_section(parts, "Uncertain", uncertains, _render_uncertain)
    text = "\n".join(parts).rstrip() + "\n"
    return text


def _append_section(
    parts: list[str],
    heading: str,
    items: Sequence[TriageItem],
    render_item: Callable[[TriageItem], list[str]],
) -> None:
    """Append ``## heading`` and items when the section is non-empty."""
    if not items:
        return
    parts.append(f"## {heading}")
    parts.append("")
    for item in items:
        parts.extend(render_item(item))
        parts.append("")


def _render_create(item: TriageItem) -> list[str]:
    """Render one Create block. Repo is never ``unknown`` here."""
    lines = [
        f"### {item.title}",
        f"- Repo: {item.repository}",
        f"- Problems: {_format_problems(item.problem_ids)}",
    ]
    _append_priority(lines, item)
    lines.extend(_format_body(item.body))
    return lines


def _render_skip(item: TriageItem) -> list[str]:
    """Render one Skip block with Existing set."""
    lines = [
        f"### {item.title}",
        f"- Repo: {item.repository}",
        f"- Existing: {item.existing_issue}",
        f"- Problems: {_format_problems(item.problem_ids)}",
    ]
    _append_priority(lines, item)
    if item.body.strip():
        lines.extend(_format_body(item.body))
    return lines


def _render_uncertain(item: TriageItem) -> list[str]:
    """Render one Uncertain block. Missing repo becomes ``unknown``."""
    repo = str(item.repository) if item.repository is not None else "unknown"
    lines = [
        f"### {item.title}",
        f"- Repo: {repo}",
        f"- Problems: {_format_problems(item.problem_ids)}",
    ]
    _append_priority(lines, item)
    if item.rationale:
        lines.append(f"- Reason: {item.rationale}")
    return lines


def _append_priority(lines: list[str], item: TriageItem) -> None:
    """Include Priority only when the item has a value. Invalid values still render."""
    if item.priority:
        lines.append(f"- Priority: {item.priority}")


def _format_problems(problem_ids: tuple[int, ...]) -> str:
    """Format ids as ``#101, #102``."""
    return ", ".join(f"#{pid}" for pid in problem_ids)


def _format_body(body: str) -> list[str]:
    """Render Body as a literal block so GFM line breaks survive editing."""
    lines = ["- Body: |"]
    content = body.rstrip("\n")
    if not content:
        return lines
    for line in content.split("\n"):
        lines.append(f"    {line}")
    return lines
