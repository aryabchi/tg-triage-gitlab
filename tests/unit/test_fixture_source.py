"""Fixture knowledge source loads invented snapshots and fails if files are missing."""

from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest

from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import RepositoryId, SnippetKind
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.ports.knowledge import MissingFixtureError

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "github"
REPOS = load_yaml_config(DEFAULT_YAML_PATH).repositories


def test_three_repos_load_and_include_export_issue() -> None:
    pack = FixtureKnowledgeSource(FIXTURE_ROOT, REPOS).collect()
    locators = {str(snippet.locator) for snippet in pack.snippets}
    assert locators == {
        "acme/sales-dashboard",
        "acme/crm",
        "acme/customer-portal",
    }
    issues = [
        snippet
        for snippet in pack.snippets
        if snippet.kind == SnippetKind.ISSUE
    ]
    assert any(
        snippet.locator == RepositoryId.parse("acme/sales-dashboard")
        and "Экспорт дашборда продаж не завершается" in snippet.title
        and "спиннер" in snippet.text
        for snippet in issues
    )


def test_missing_issues_json_fails(tmp_path: Path) -> None:
    copied = copytree(FIXTURE_ROOT, tmp_path / "github")
    (copied / "acme" / "sales-dashboard" / "issues.json").unlink()
    source = FixtureKnowledgeSource(copied, REPOS)
    with pytest.raises(MissingFixtureError) as exc_info:
        source.collect()
    assert exc_info.value.repository == RepositoryId.parse("acme/sales-dashboard")
    assert exc_info.value.path.endswith("issues.json")


def test_missing_repo_directory_fails(tmp_path: Path) -> None:
    root = tmp_path / "github"
    root.mkdir()
    missing = RepositoryId.parse("acme/sales-dashboard")
    source = FixtureKnowledgeSource(root, (missing,))
    with pytest.raises(MissingFixtureError) as exc_info:
        source.collect()
    assert exc_info.value.repository == missing
    assert "sales-dashboard" in exc_info.value.path
