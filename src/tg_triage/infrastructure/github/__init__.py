"""GitHub REST adapters. Tokens and URLs stay out of the domain."""

from tg_triage.infrastructure.github.client import GitHubClient, github_http_client
from tg_triage.infrastructure.github.issue_tracker import GitHubIssueTracker

__all__ = ["GitHubClient", "GitHubIssueTracker", "github_http_client"]
