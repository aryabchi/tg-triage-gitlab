"""Live GitHub create smoke. Opt-in; skipped in default pytest.

Creates one throwaway issue on a configured demo repo under ``GITHUB_OWNER``.
Does not seed, refresh, or drop. Coding agents must not invent a token.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tg_triage.config import AppConfig, MissingSettingsError
from tg_triage.infrastructure.github.issue_tracker import GitHubIssueTracker
from tg_triage.operator.runtime import load_operator_config, open_github_client, target_repositories

pytestmark = pytest.mark.smoke


@pytest.fixture
def live_github() -> AppConfig:
    """Load operator config after the opt-in flag. Skip if secrets are absent."""
    if os.environ.get("RUN_GITHUB_SMOKE") != "1":
        pytest.skip("set RUN_GITHUB_SMOKE=1 to run live GitHub smoke")
    config = load_operator_config()
    try:
        config.require("github_token", "github_owner")
    except MissingSettingsError as exc:
        pytest.skip(str(exc))
    return config


def test_create_issue_on_seeded_demo_repo_returns_live_url(live_github: AppConfig) -> None:
    """Create on ``sales-dashboard`` only and GET the returned html_url."""
    token = live_github.settings.github_token
    if token is None:
        pytest.skip("GITHUB_TOKEN required")
    allowed = target_repositories(live_github)
    repository = next(repo for repo in allowed if str(repo).endswith("/sales-dashboard"))
    http, client = open_github_client(token)
    try:
        created = GitHubIssueTracker(client).create(
            repository,
            "tg-triage smoke",
            "Throwaway issue from tests/smoke/test_github_live.py.",
        )
        print(created.url)
        page = httpx.get(
            created.url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "tg-triage"},
            follow_redirects=True,
            timeout=30.0,
        )
        assert page.status_code == 200, created.url
    finally:
        http.close()
