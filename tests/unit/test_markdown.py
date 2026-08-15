"""Markdown contract: golden parse, round-trip, and fail-closed validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import IssueRef, RepositoryId, TriageOutcome
from tg_triage.markdown import PlanValidationError, parse, parse_and_validate, render

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "contract_example.md"
ALLOWED = load_yaml_config(DEFAULT_YAML_PATH).repositories


def test_parse_golden_file() -> None:
    plan = parse_and_validate(GOLDEN.read_text(encoding="utf-8"), ALLOWED)
    assert plan.run_id == 7
    assert plan.since_date is not None
    assert plan.since_date.isoformat() == "2026-08-13"
    assert len(plan.items) == 3

    create, skip, uncertain = plan.items
    assert create.outcome == TriageOutcome.CREATE_NEW
    assert create.repository == RepositoryId.parse("acme/sales-dashboard")
    assert create.problem_ids == (101, 102)
    assert create.title == "Sales dashboard export hangs"
    assert create.priority == "P1"
    assert "spinner" in create.body
    assert "2 users" in create.body

    assert skip.outcome == TriageOutcome.LINK_EXISTING
    assert skip.existing_issue == IssueRef.parse("acme/sales-dashboard#81")
    assert skip.problem_ids == (104,)
    assert skip.repository == RepositoryId.parse("acme/sales-dashboard")

    assert uncertain.outcome == TriageOutcome.UNCERTAIN
    assert uncertain.problem_ids == (103,)
    assert uncertain.repository is None
    assert "crm" in uncertain.rationale


def test_render_parse_round_trip() -> None:
    original = parse_and_validate(GOLDEN.read_text(encoding="utf-8"), ALLOWED)
    assert original.run_id is not None
    assert original.since_date is not None
    rendered = render(original.items, run_id=original.run_id, since_date=original.since_date)
    again = parse_and_validate(rendered, ALLOWED)
    assert again.items == original.items
    assert again.run_id == original.run_id
    assert again.since_date == original.since_date


def test_unknown_section_fails() -> None:
    text = GOLDEN.read_text(encoding="utf-8").replace("## Skip", "## Foo")
    with pytest.raises(PlanValidationError, match="unknown section"):
        parse_and_validate(text, ALLOWED)


def test_create_with_unknown_repo_fails() -> None:
    text = GOLDEN.read_text(encoding="utf-8").replace(
        "- Repo: acme/sales-dashboard",
        "- Repo: unknown",
        1,
    )
    with pytest.raises(PlanValidationError, match="not unknown"):
        parse_and_validate(text, ALLOWED)


def test_skip_without_existing_fails() -> None:
    text = GOLDEN.read_text(encoding="utf-8").replace(
        "- Existing: acme/sales-dashboard#81\n",
        "",
    )
    with pytest.raises(PlanValidationError, match="Existing"):
        parse_and_validate(text, ALLOWED)


def test_owner_moves_uncertain_into_create() -> None:
    text = """\
# GitHub Issues
# run: 7
# since: 2026-08-13

## Legend

ignored

## Create

### Customer synchronization is delayed
- Repo: acme/crm
- Problems: #103
- Body: |
    Clients sync is delayed.
"""
    plan = parse_and_validate(text, ALLOWED)
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.outcome == TriageOutcome.CREATE_NEW
    assert item.repository == RepositoryId.parse("acme/crm")
    assert item.problem_ids == (103,)
    assert "delayed" in item.body
