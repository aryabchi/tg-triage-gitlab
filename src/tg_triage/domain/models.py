"""Immutable domain types and closed enumerations.

Invariants live on the value objects (repository form, issue numbers, lifecycle
membership). Persistence and adapters must not introduce extra Problem statuses
or tracker-specific types here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class ProblemLifecycle(StrEnum):
    """Whether a report may still enter a compile.

    ``ingested`` stays eligible across failed, cancelled, superseded, uncertain,
    and excluded runs. ``linked`` is terminal after a successful create or skip.
    Compile itself never changes this value.
    """

    INGESTED = "ingested"
    LINKED = "linked"


class TriageRunStatus(StrEnum):
    """Workflow state of one compile request.

    Confirm is not a status: the run stays ``awaiting_execute`` until confirm or
    cancel. A newer compile marks a previous pending or awaiting run superseded.
    """

    PENDING = "pending"
    AWAITING_EXECUTE = "awaiting_execute"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class TriageOutcome(StrEnum):
    """What should happen for one clustered item.

    ``exclude`` is an owner edit (deleted block), not an LLM recommendation.
    Uncertain is an item outcome, not a parallel collection of reports.
    """

    CREATE_NEW = "create_new"
    LINK_EXISTING = "link_existing"
    UNCERTAIN = "uncertain"
    EXCLUDE = "exclude"


class SnippetKind(StrEnum):
    """Provenance kind inside an evidence pack. Unknown kinds are not used."""

    REPO_META = "repo_meta"
    README = "readme"
    ISSUE = "issue"


class ExecutionAction(StrEnum):
    """Tracker side effect recorded after confirm. Uncertain and exclude are omitted."""

    CREATE = "create"
    SKIP = "skip"


def _is_owner_repo(value: str) -> bool:
    """Return True when ``value`` is exactly one non-empty owner and one non-empty name."""
    if value.count("/") != 1:
        return False
    owner, name = value.split("/")
    return bool(owner) and bool(name)


@dataclass(frozen=True, slots=True)
class RepositoryId:
    """Closed-world tracker identity in ``owner/repo`` form.

    Empty strings, missing slashes, extra path segments, and empty owner or
    name are rejected.

    Raises:
        ValueError: If ``value`` is not ``owner/repo``.
    """

    value: str

    def __post_init__(self) -> None:
        if not _is_owner_repo(self.value):
            raise ValueError(f"invalid repository id: {self.value!r}")

    @classmethod
    def parse(cls, raw: str) -> RepositoryId:
        """Build from a string, stripping surrounding whitespace.

        Raises:
            ValueError: If the stripped value is not ``owner/repo``.
        """
        return cls(raw.strip())

    def __str__(self) -> str:
        """Return the canonical ``owner/repo`` form accepted by ``parse``."""
        return self.value


@dataclass(frozen=True, slots=True)
class IssueRef:
    """A numbered issue on a repository, written ``owner/repo#n``.

    ``n`` must be an integer greater than or equal to 1.
    """

    repository: RepositoryId
    number: int

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"invalid issue number: {self.number}")

    @classmethod
    def parse(cls, raw: str) -> IssueRef:
        """Parse ``owner/repo#n``.

        Raises:
            ValueError: If ``#`` or the number is missing, the repository form
                is invalid, or the number is less than 1.
        """
        repo_part, sep, number_part = raw.partition("#")
        if not sep or not number_part:
            raise ValueError(f"invalid issue ref: {raw!r}")
        return cls(RepositoryId.parse(repo_part), int(number_part))

    def __str__(self) -> str:
        """Return the canonical ``owner/repo#n`` form accepted by ``parse``."""
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True, slots=True)
class Problem:
    """One original user report, preserved verbatim.

    ``message_id`` and ``chat_id`` uniquely identify the source message for
    idempotent intake. ``linked_issue`` is set only when execute creates or
    skips; several reports in one clustered item share the same ref.
    """

    id: int
    original_text: str
    message_id: int
    chat_id: int
    user_id: int
    created_at: datetime
    lifecycle: ProblemLifecycle = ProblemLifecycle.INGESTED
    linked_issue: IssueRef | None = None


@dataclass(frozen=True, slots=True)
class TriageItem:
    """One validated cluster/match recommendation stored on a run for audit.

    Execute does not read this object; the uploaded Markdown is the authority.
    ``repository`` is ``None`` when the outcome is unknown. ``problem_ids``
    participate in the coverage partition for the compile period.
    """

    problem_ids: tuple[int, ...]
    outcome: TriageOutcome
    title: str = ""
    body: str = ""
    rationale: str = ""
    repository: RepositoryId | None = None
    existing_issue: IssueRef | None = None
    priority: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSnippet:
    """One piece of tracker snapshot text with enough locator to validate identities.

    ``issue_number`` is set only for ``kind=issue``. ``provenance`` is for humans
    (path or locator), not for retrieval.
    """

    source_id: str
    kind: SnippetKind
    locator: RepositoryId
    title: str
    text: str
    provenance: str
    issue_number: int | None = None


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Full closed-world snapshot consumed by match. Missing files fail compile."""

    snippets: tuple[EvidenceSnippet, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionItemResult:
    """One create or skip after confirm, used to avoid duplicate creates on retry.

    ``url`` is present for create when the tracker returned an address. Skip
    records the existing ref and does not write to the tracker.
    """

    action: ExecutionAction
    issue: IssueRef
    problem_ids: tuple[int, ...]
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """All create/skip outcomes persisted on the run. Uncertain/exclude are absent."""

    items: tuple[ExecutionItemResult, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkdownPlan:
    """In-memory execution plan parsed from the owner's uploaded file.

    Exists only between preview and confirm. It is not a second stored decision
    model; the uploaded bytes remain the authority.
    """

    items: tuple[TriageItem, ...] = ()
    run_id: int | None = None
    since_date: date | None = None


@dataclass(frozen=True, slots=True)
class TriageRun:
    """One compile: generated file, optional owner upload, items JSON, and execute result.

    ``uploaded_markdown`` is the only execute input once present.
    ``generated_markdown`` becomes stale after upload. ``items`` is audit data
    from compile, not execute authority.
    """

    id: int
    since_date: date
    owner_user_id: int
    status: TriageRunStatus
    generated_markdown: bytes | None = None
    uploaded_markdown: bytes | None = None
    items: tuple[TriageItem, ...] = ()
    execution_result: ExecutionResult | None = None
    created_at: datetime | None = None
