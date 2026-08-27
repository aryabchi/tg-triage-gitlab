"""Domain invariants: coverage partition, repository ids, issue refs, lifecycle."""

from __future__ import annotations

import pytest

from tg_triage.domain import (
    CoverageError,
    IssueRef,
    ProblemLifecycle,
    RepositoryId,
    TriageItem,
    TriageOutcome,
    assert_coverage,
)


def _item(*problem_ids: int) -> TriageItem:
    return TriageItem(problem_ids=problem_ids, outcome=TriageOutcome.UNCERTAIN)


def test_coverage_partition_success() -> None:
    assert_coverage([101, 102, 103], [_item(101, 102), _item(103)])


def test_coverage_missing_id() -> None:
    with pytest.raises(CoverageError) as exc_info:
        assert_coverage([101, 102, 103], [_item(101), _item(103)])
    assert 102 in exc_info.value.missing
    assert not exc_info.value.extra
    assert not exc_info.value.duplicate


def test_coverage_duplicate_id() -> None:
    with pytest.raises(CoverageError) as exc_info:
        assert_coverage([101, 102], [_item(101, 102), _item(102)])
    assert 102 in exc_info.value.duplicate


def test_coverage_extra_id() -> None:
    with pytest.raises(CoverageError) as exc_info:
        assert_coverage([101], [_item(101, 999)])
    assert 999 in exc_info.value.extra


def test_issue_ref_round_trip() -> None:
    raw = "owner/repo#81"
    parsed = IssueRef.parse(raw)
    assert str(parsed) == raw
    assert parsed.repository == RepositoryId.parse("owner/repo")
    assert parsed.number == 81


def test_repository_id_rejects_empty() -> None:
    with pytest.raises(ValueError, match="invalid repository id"):
        RepositoryId.parse("")
    with pytest.raises(ValueError, match="invalid repository id"):
        RepositoryId.parse("   ")


def test_repository_id_rejects_non_owner_repo_form() -> None:
    with pytest.raises(ValueError, match="invalid repository id"):
        RepositoryId.parse("sales-dashboard")
    with pytest.raises(ValueError, match="invalid repository id"):
        RepositoryId.parse("/crm")
    with pytest.raises(ValueError, match="invalid repository id"):
        RepositoryId.parse("acme/")
    with pytest.raises(ValueError, match="invalid repository id"):
        RepositoryId.parse("acme/crm/extra")


def test_lifecycle_has_exactly_two_values() -> None:
    assert set(ProblemLifecycle) == {
        ProblemLifecycle.INGESTED,
        ProblemLifecycle.LINKED,
    }
    assert {member.value for member in ProblemLifecycle} == {"ingested", "linked"}
