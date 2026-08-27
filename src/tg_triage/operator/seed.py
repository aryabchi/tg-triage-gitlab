"""Create-once seed of configured fake demo repositories.

Does not write fixture files. If a target already exists, it is skipped and no
issues are added.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, cast

from tg_triage.config import MissingSettingsError
from tg_triage.domain import RepositoryId
from tg_triage.infrastructure.github.client import GitHubClient
from tg_triage.operator.runtime import (
    DEFAULT_SEED_ROOT,
    load_operator_config,
    open_github_client,
    seed_lookup_owner,
    target_repositories,
)


def seed_repositories(
    client: GitHubClient,
    repositories: Sequence[RepositoryId],
    seed_root: Path,
    *,
    seed_owner: str | None = None,
    output: TextIO = sys.stdout,
) -> None:
    """Create missing repos from versioned seed files under ``seed_root``.

    ``seed_owner`` selects the seed directory owner when the live owner differs
    from the checked-in ``acme`` tree.
    """
    for repository in repositories:
        _seed_one(client, repository, seed_root, seed_owner=seed_owner, output=output)


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
        seed_repositories(
            client,
            target_repositories(config),
            DEFAULT_SEED_ROOT,
            seed_owner=seed_lookup_owner(config),
        )
    finally:
        http.close()
    return 0


def _seed_one(
    client: GitHubClient,
    repository: RepositoryId,
    seed_root: Path,
    *,
    seed_owner: str | None,
    output: TextIO,
) -> None:
    """Create one repo from seed files, or print a skip message if it exists."""
    if client.get_repo(repository) is not None:
        print(
            f"Skipping {repository}: repository already exists. "
            "Run tg-triage-drop, then seed, to recreate.",
            file=output,
        )
        return
    directory = _seed_dir(seed_root, repository, seed_owner=seed_owner)
    meta_path = directory / "meta.json"
    readme_path = directory / "README.md"
    issues_path = directory / "issues.json"
    for path in (meta_path, readme_path, issues_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    description = _description(meta_path)
    client.create_repo(repository, description)
    client.set_description(repository, description)
    client.put_readme(repository, readme_path.read_text(encoding="utf-8"))
    for title, body in _seed_issues(issues_path):
        client.create_issue(repository, title, body)


def _seed_dir(seed_root: Path, repository: RepositoryId, *, seed_owner: str | None) -> Path:
    """Resolve ``seed_root/<owner>/<name>/`` for this repository."""
    owner, name = repository.value.split("/", 1)
    return seed_root / (seed_owner or owner) / name


def _description(meta_path: Path) -> str:
    """Read ``description`` from seed ``meta.json``."""
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"meta.json must be an object: {meta_path}")
    mapping = cast(dict[str, object], data)
    return str(mapping.get("description", ""))


def _seed_issues(path: Path) -> list[tuple[str, str]]:
    """Load title/body pairs. ``number`` in the file is ignored if present."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"issues.json must be a list: {path}")
    issues: list[tuple[str, str]] = []
    for raw in data:
        if not isinstance(raw, dict):
            raise ValueError(f"issue entry must be an object: {path}")
        item = cast(dict[str, object], raw)
        issues.append((str(item.get("title", "")), str(item.get("body", ""))))
    return issues


if __name__ == "__main__":
    raise SystemExit(main())
