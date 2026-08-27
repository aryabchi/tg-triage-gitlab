"""Markdown contract: one schema for recommendation, owner edit, and execute.

The renderer is canonical. The parser is the only path from an uploaded file
to an execution plan. Section headings are the action; there is no per-item
Action field.
"""

from collections.abc import Collection

from tg_triage.domain import MarkdownPlan, RepositoryId
from tg_triage.markdown.parser import PlanValidationError, parse
from tg_triage.markdown.renderer import render
from tg_triage.markdown.validate_plan import validate_plan


def parse_and_validate(
    text: str,
    allowed_repos: Collection[RepositoryId],
) -> MarkdownPlan:
    """Parse text then apply repo and required-field rules.

    Raises:
        PlanValidationError: If the file or items are not executable.
    """
    plan = parse(text)
    validate_plan(plan, allowed_repos)
    return plan


__all__ = [
    "PlanValidationError",
    "parse",
    "parse_and_validate",
    "render",
    "validate_plan",
]
