"""Load an EvidencePack from ``fixtures/github/<owner>/<repo>/`` files.

Required files per repository: ``meta.json``, ``README.md``, ``issues.json``.
Missing any of them fails closed. This adapter does not call a tracker API.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from tg_triage.domain import EvidencePack, EvidenceSnippet, RepositoryId, SnippetKind
from tg_triage.ports.knowledge import MissingFixtureError

SOURCE_ID = "github-fixtures"


class FixtureKnowledgeSource:
    """Disk snapshot used by compile. ``root`` is the ``.../github`` directory."""

    def __init__(
        self,
        root: Path,
        repositories: Sequence[RepositoryId],
        *,
        k: int = 10,
        readme_max_chars: int = 2000,
        issue_body_max_chars: int = 1000,
    ) -> None:
        self._root = root
        self._repositories = tuple(repositories)
        self._k = k
        self._readme_max_chars = readme_max_chars
        self._issue_body_max_chars = issue_body_max_chars

    def collect(self) -> EvidencePack:
        """Build snippets for repo meta, README excerpt, and up to ``k`` issues.

        Raises:
            MissingFixtureError: If a configured repo directory or file is missing.
            ValueError: If ``meta.json`` or ``issues.json`` is not the expected shape.
        """
        snippets: list[EvidenceSnippet] = []
        for repository in self._repositories:
            snippets.extend(self._collect_repo(repository))
        return EvidencePack(snippets=tuple(snippets))

    def _collect_repo(self, repository: RepositoryId) -> list[EvidenceSnippet]:
        """Load one ``owner/repo`` directory into snippets."""
        owner, name = repository.value.split("/", 1)
        directory = self._root / owner / name
        if not directory.is_dir():
            raise MissingFixtureError(repository, str(directory))
        meta_path = directory / "meta.json"
        readme_path = directory / "README.md"
        issues_path = directory / "issues.json"
        for path in (meta_path, readme_path, issues_path):
            if not path.is_file():
                raise MissingFixtureError(repository, str(path))
        return [
            self._meta_snippet(repository, meta_path),
            self._readme_snippet(repository, readme_path),
            *self._issue_snippets(repository, issues_path),
        ]

    def _meta_snippet(self, repository: RepositoryId, path: Path) -> EvidenceSnippet:
        """Repo description from ``meta.json``."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"meta.json must be an object: {path}")
        mapping = cast(dict[str, object], data)
        description = str(mapping.get("description", ""))
        return EvidenceSnippet(
            source_id=SOURCE_ID,
            kind=SnippetKind.REPO_META,
            locator=repository,
            title=str(repository),
            text=description,
            provenance=str(path),
        )

    def _readme_snippet(self, repository: RepositoryId, path: Path) -> EvidenceSnippet:
        """README excerpt truncated to the configured character budget."""
        text = path.read_text(encoding="utf-8")[: self._readme_max_chars]
        return EvidenceSnippet(
            source_id=SOURCE_ID,
            kind=SnippetKind.README,
            locator=repository,
            title="README",
            text=text,
            provenance=str(path),
        )

    def _issue_snippets(self, repository: RepositoryId, path: Path) -> list[EvidenceSnippet]:
        """Newest-open issues from ``issues.json``, truncated in count and body."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"issues.json must be a list: {path}")
        snippets: list[EvidenceSnippet] = []
        for raw in data[: self._k]:
            if not isinstance(raw, dict):
                raise ValueError(f"issue entry must be an object: {path}")
            issue = cast(dict[str, object], raw)
            number = int(issue["number"])
            title = str(issue.get("title", ""))
            body = str(issue.get("body", ""))[: self._issue_body_max_chars]
            snippets.append(
                EvidenceSnippet(
                    source_id=SOURCE_ID,
                    kind=SnippetKind.ISSUE,
                    locator=repository,
                    title=title,
                    text=body,
                    provenance=str(path),
                    issue_number=number,
                )
            )
        return snippets
