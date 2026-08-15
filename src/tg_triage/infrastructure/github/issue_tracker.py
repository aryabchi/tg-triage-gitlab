"""IssueTracker adapter over GitHub issue create."""

from __future__ import annotations

from tg_triage.domain import IssueRef, RepositoryId
from tg_triage.infrastructure.github.client import GitHubClient
from tg_triage.ports.issue_tracker import CreatedIssue


class GitHubIssueTracker:
    """Maps GitHub ``number`` and ``html_url`` onto domain create results."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def create(self, repository: RepositoryId, title: str, body: str) -> CreatedIssue:
        """Open an issue with title and body only.

        Raises:
            KeyError: If the GitHub response omits ``number`` or ``html_url``.
            TypeError: If those fields are the wrong JSON types.
        """
        payload = self._client.create_issue(repository, title, body)
        number = payload["number"]
        url = payload["html_url"]
        if not isinstance(number, int):
            raise TypeError("GitHub issue number must be an int")
        if not isinstance(url, str):
            raise TypeError("GitHub html_url must be a string")
        return CreatedIssue(ref=IssueRef(repository, number), url=url)
