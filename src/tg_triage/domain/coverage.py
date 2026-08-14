"""Coverage: every in-period Problem id appears in exactly one item."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from tg_triage.domain.models import TriageItem


class CoverageError(ValueError):
    """The items do not partition the expected problem ids.

    Attributes:
        missing: Ids that were expected but appear in no item.
        extra: Ids that appear in items but were not expected.
        duplicate: Ids that appear more than once across items.
    """

    def __init__(
        self,
        *,
        missing: frozenset[int],
        extra: frozenset[int],
        duplicate: frozenset[int],
    ) -> None:
        self.missing = missing
        self.extra = extra
        self.duplicate = duplicate
        parts: list[str] = []
        if missing:
            parts.append(f"missing={sorted(missing)}")
        if extra:
            parts.append(f"extra={sorted(extra)}")
        if duplicate:
            parts.append(f"duplicate={sorted(duplicate)}")
        super().__init__("coverage failed: " + ", ".join(parts))


def assert_coverage(problem_ids: Iterable[int], items: Sequence[TriageItem]) -> None:
    """Require a partition of ``problem_ids`` across ``items``.

    Each expected id must appear in exactly one item. Duplicate ids inside a
    single item also fail.

    Raises:
        CoverageError: On missing, extra, or duplicate ids.
    """
    expected = set(problem_ids)
    counted: Counter[int] = Counter()
    for item in items:
        counted.update(item.problem_ids)
    seen = set(counted)
    duplicate = frozenset(pid for pid, n in counted.items() if n > 1)
    missing = frozenset(expected - seen)
    extra = frozenset(seen - expected)
    if missing or extra or duplicate:
        raise CoverageError(missing=missing, extra=extra, duplicate=duplicate)
