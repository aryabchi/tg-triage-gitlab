"""Read configured fake repos and write fixture snapshot files.

Does not create, update, or delete GitHub repositories or issues.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tg_triage.config import MissingSettingsError
from tg_triage.domain import RepositoryId
from tg_triage.infrastructure.github.client import GitHubClient
from tg_triage.operator.runtime import (
    DEFAULT_FIXTURE_ROOT,
    load_operator_config,
    open_github_client,
    target_repositories,
)


def refresh_fixtures(
    client: GitHubClient,
    repositories: Sequence[RepositoryId],
    fixture_root: Path,
    *,
    k: int = 10,
    readme_max_chars: int = 2000,
    issue_body_max_chars: int = 1000,
) -> None:
    """Write ``meta.json``, ``README.md``, and ``issues.json`` per repository."""
    for repository in repositories:
        owner, name = repository.value.split("/", 1)
        directory = fixture_root / owner / name
        directory.mkdir(parents=True, exist_ok=True)
        repo = client.get_repo(repository)
        if repo is None:
            raise FileNotFoundError(f"GitHub repository missing: {repository}")
        description = str(repo.get("description") or "")
        (directory / "meta.json").write_text(
            json.dumps({"description": description}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        readme = client.get_readme(repository)[:readme_max_chars]
        (directory / "README.md").write_text(readme, encoding="utf-8")
        issues = []
        for item in client.list_open_issues(repository)[:k]:
            number = item.get("number")
            if not isinstance(number, int):
                raise TypeError(f"issue number must be an int: {repository}")
            issues.append(
                {
                    "number": number,
                    "title": str(item.get("title", "")),
                    "body": str(item.get("body") or "")[:issue_body_max_chars],
                }
            )
        (directory / "issues.json").write_text(
            json.dumps(issues, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    """CLI entry point. Requires ``GITHUB_TOKEN``."""
    try:
        config = load_operator_config()
        config.require("github_token")
    except MissingSettingsError as exc:
        print(exc, file=sys.stderr)
        return 1
    token = config.settings.github_token
    assert token is not None
    http, client = open_github_client(token)
    try:
        refresh_fixtures(
            client,
            target_repositories(config),
            DEFAULT_FIXTURE_ROOT,
            k=config.yaml.k,
            readme_max_chars=config.yaml.readme_max_chars,
            issue_body_max_chars=config.yaml.issue_body_max_chars,
        )
    finally:
        http.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
