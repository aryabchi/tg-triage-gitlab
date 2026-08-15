"""Knowledge port: compile reads a snapshot, never a live tracker."""

from __future__ import annotations

from typing import Protocol

from tg_triage.domain import EvidencePack, RepositoryId


class MissingFixtureError(Exception):
    """A configured repository is missing a snapshot directory or required file.

    Compile must fail the run when this is raised. ``path`` is the missing
    directory or file.
    """

    def __init__(self, repository: RepositoryId, path: str) -> None:
        self.repository = repository
        self.path = path
        super().__init__(f"missing fixture for {repository}: {path}")


class KnowledgeSource(Protocol):
    """Closed-world evidence for match. The MVP implementation reads fixture files."""

    def collect(self) -> EvidencePack:
        """Return the full snapshot for every configured repository.

        Raises:
            MissingFixtureError: If any configured repo directory or required
                file is absent.
        """
