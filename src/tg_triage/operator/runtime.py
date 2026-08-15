"""Shared config and HTTP wiring for operator GitHub scripts."""

from __future__ import annotations

import httpx

from tg_triage.config import REPO_ROOT, AppConfig, load_config
from tg_triage.domain import RepositoryId
from tg_triage.infrastructure.github.client import GitHubClient, github_http_client

DEFAULT_SEED_ROOT = REPO_ROOT / "seed" / "github"
DEFAULT_FIXTURE_ROOT = REPO_ROOT / "fixtures" / "github"


def load_operator_config() -> AppConfig:
    """Load YAML plus env. Reads a repo-root ``.env`` only when that file exists."""
    env_file = REPO_ROOT / ".env"
    return load_config(env_file=env_file if env_file.is_file() else None)


def target_repositories(config: AppConfig) -> tuple[RepositoryId, ...]:
    """Closed-world list, with ``GITHUB_OWNER`` replacing the YAML owner when set."""
    owner = config.settings.github_owner
    if not owner:
        return config.repositories
    return tuple(
        RepositoryId.parse(f"{owner}/{repo.value.split('/', 1)[1]}")
        for repo in config.repositories
    )


def seed_lookup_owner(config: AppConfig) -> str:
    """YAML owner used to find ``seed/github/<owner>/<name>/`` definitions."""
    if not config.repositories:
        raise ValueError("config has no repositories")
    return config.repositories[0].value.split("/", 1)[0]


def open_github_client(token: str) -> tuple[httpx.Client, GitHubClient]:
    """Return an HTTP client and wrapper. Caller must close the HTTP client."""
    http = github_http_client(token)
    return http, GitHubClient(http)
