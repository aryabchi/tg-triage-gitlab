"""Turn eligible group text into a persisted Problem.

This use case does not decide what is eligible. Callers must not pass commands
or documents. Duplicate ``(chat_id, message_id)`` returns the existing row.
"""

from __future__ import annotations

from datetime import datetime

from tg_triage.domain import Problem, ProblemLifecycle
from tg_triage.ports.repositories import ProblemRepository


def ingest_group_text(
    repo: ProblemRepository,
    text: str,
    user_id: int,
    message_id: int,
    chat_id: int,
    created_at: datetime,
) -> Problem:
    """Store one report as ``ingested`` with the original text unchanged.

    Returns:
        The stored row. A second call with the same chat and message ids
        returns that first row and does not insert another.
    """
    return repo.insert(
        Problem(
            id=0,
            original_text=text,
            message_id=message_id,
            chat_id=chat_id,
            user_id=user_id,
            created_at=created_at,
            lifecycle=ProblemLifecycle.INGESTED,
            linked_issue=None,
        )
    )
