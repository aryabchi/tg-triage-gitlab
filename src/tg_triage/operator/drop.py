"""Delete the configured fake demo repositories after explicit confirmation.

Never deletes a repository that is not on the configured list. There is no
``--yes`` flag; the operator must confirm after seeing every ``owner/repo``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

from tg_triage.config import MissingSettingsError
from tg_triage.domain import RepositoryId
from tg_triage.infrastructure.github.client import GitHubClient
from tg_triage.operator.runtime import (
    load_operator_config,
    open_github_client,
    target_repositories,
)


def confirmation_matches(reply: str, repositories: Sequence[RepositoryId]) -> bool:
    """True when the operator typed ``yes`` or the exact ``owner/repo`` list."""
    text = reply.strip()
    if text.lower() == "yes":
        return True
    tokens = [part for part in text.replace(",", " ").split() if part]
    return set(tokens) == {str(repo) for repo in repositories}


def drop_repositories(
    client: GitHubClient,
    repositories: Sequence[RepositoryId],
    *,
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
) -> bool:
    """Print the delete list, wait for confirmation, then DELETE only those repos.

    Returns:
        True if deletes were sent, False if confirmation did not match.
    """
    print("These repositories will be deleted:", file=output)
    for repository in repositories:
        print(f"- {repository}", file=output)
    print(
        "Type yes or the full list of owner/repo names to confirm:",
        file=output,
    )
    reply = input_stream.readline()
    if not confirmation_matches(reply, repositories):
        print("Aborted. No repositories were deleted.", file=output)
        return False
    for repository in repositories:
        client.delete_repo(repository)
    return True


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
        dropped = drop_repositories(client, target_repositories(config))
    finally:
        http.close()
    return 0 if dropped else 1


if __name__ == "__main__":
    raise SystemExit(main())
