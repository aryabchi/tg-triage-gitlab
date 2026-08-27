"""GitHub adapters and operator scripts against MockTransport. No live GitHub."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from tg_triage.config import DEFAULT_YAML_PATH, REPO_ROOT, load_yaml_config
from tg_triage.domain import RepositoryId
from tg_triage.infrastructure.github.client import GitHubClient, github_http_client
from tg_triage.infrastructure.github.issue_tracker import GitHubIssueTracker
from tg_triage.operator.drop import drop_repositories
from tg_triage.operator.refresh import refresh_fixtures
from tg_triage.operator.seed import seed_repositories

SEED_ROOT = REPO_ROOT / "seed" / "github"
REPOS = load_yaml_config(DEFAULT_YAML_PATH).repositories
SALES = RepositoryId.parse("acme/sales-dashboard")


@dataclass
class RecordedCall:
    """One mocked GitHub HTTP request."""

    method: str
    path: str
    json: object | None


@dataclass
class GitHubMock:
    """Stateful GitHub API stand-in for seed, refresh, and drop tests."""

    calls: list[RecordedCall] = field(default_factory=list)
    existing: set[str] = field(default_factory=set)
    descriptions: dict[str, str] = field(default_factory=dict)
    readmes: dict[str, str] = field(default_factory=dict)
    issues: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    next_number: int = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Route a mocked GitHub REST request."""
        path = request.url.path
        method = request.method
        payload = json.loads(request.content.decode("utf-8")) if request.content else None
        self.calls.append(RecordedCall(method=method, path=path, json=payload))
        repo_key = _repo_from_path(path)

        if method == "GET" and path.startswith("/repos/") and path.count("/") == 3:
            if repo_key in self.existing:
                return httpx.Response(
                    200,
                    json={"description": self.descriptions.get(repo_key, "")},
                )
            return httpx.Response(404, json={"message": "Not Found"})

        if method == "POST" and (path == "/user/repos" or path.startswith("/orgs/")):
            name = str(payload["name"]) if isinstance(payload, dict) else ""
            owner = "acme" if path == "/user/repos" else path.split("/")[2]
            key = f"{owner}/{name}"
            self.existing.add(key)
            if isinstance(payload, dict):
                self.descriptions[key] = str(payload.get("description", ""))
            return httpx.Response(201, json={"full_name": key})

        if method == "PATCH" and path.startswith("/repos/") and repo_key:
            if isinstance(payload, dict):
                self.descriptions[repo_key] = str(payload.get("description", ""))
            return httpx.Response(200, json={"description": self.descriptions.get(repo_key, "")})

        if method == "PUT" and path.endswith("/contents/README.md") and repo_key:
            content = payload["content"] if isinstance(payload, dict) else ""
            self.readmes[repo_key] = base64.b64decode(str(content)).decode("utf-8")
            return httpx.Response(201, json={"content": {"path": "README.md"}})

        if method == "GET" and path.endswith("/contents/README.md") and repo_key:
            text = self.readmes.get(repo_key, "")
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            return httpx.Response(200, json={"content": encoded, "encoding": "base64"})

        if method == "POST" and path.endswith("/issues") and repo_key:
            assert isinstance(payload, dict)
            assert set(payload) == {"title", "body"}
            number = self.next_number
            self.next_number += 1
            issue = {
                "number": number,
                "title": payload["title"],
                "body": payload["body"],
                "html_url": f"https://github.com/{repo_key}/issues/{number}",
            }
            self.issues.setdefault(repo_key, []).append(issue)
            return httpx.Response(201, json=issue)

        if method == "GET" and path.endswith("/issues") and repo_key:
            return httpx.Response(200, json=self.issues.get(repo_key, []))

        if method == "DELETE" and path.startswith("/repos/") and repo_key:
            self.existing.discard(repo_key)
            return httpx.Response(204)

        return httpx.Response(500, text=f"unhandled {method} {path}")


def _repo_from_path(path: str) -> str:
    """Extract ``owner/repo`` from a ``/repos/{owner}/{repo}/...`` path."""
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "repos":
        return f"{parts[1]}/{parts[2]}"
    return ""


def _client(mock: GitHubMock) -> GitHubClient:
    http = github_http_client("test-token", transport=httpx.MockTransport(mock))
    return GitHubClient(http)


def _issue_posts(mock: GitHubMock) -> list[RecordedCall]:
    return [call for call in mock.calls if call.method == "POST" and call.path.endswith("/issues")]


def _create_repo_posts(mock: GitHubMock) -> list[RecordedCall]:
    return [
        call
        for call in mock.calls
        if call.method == "POST" and call.path.endswith("/repos") and "/issues" not in call.path
    ]


def _delete_calls(mock: GitHubMock) -> list[RecordedCall]:
    return [call for call in mock.calls if call.method == "DELETE"]


def test_create_issue_posts_title_and_body_only() -> None:
    mock = GitHubMock()
    tracker = GitHubIssueTracker(_client(mock))
    created = tracker.create(SALES, "Экспорт зависает", "Спиннер не останавливается.")
    posts = _issue_posts(mock)
    assert len(posts) == 1
    assert posts[0].path == "/repos/acme/sales-dashboard/issues"
    assert posts[0].json == {
        "title": "Экспорт зависает",
        "body": "Спиннер не останавливается.",
    }
    assert created.ref.number == 1
    assert created.url.endswith("/issues/1")


def test_seed_creates_repos_and_seed_issues() -> None:
    mock = GitHubMock()
    seed_repositories(_client(mock), REPOS, SEED_ROOT)
    names = {
        call.json["name"]
        for call in _create_repo_posts(mock)
        if isinstance(call.json, dict)
    }
    assert names == {"sales-dashboard", "crm", "customer-portal"}
    posts = _issue_posts(mock)
    assert len(posts) == 1
    assert posts[0].path == "/repos/acme/sales-dashboard/issues"
    assert posts[0].json == {
        "title": "Экспорт дашборда продаж не завершается",
        "body": "Крутится спиннер, вечная загрузка при выгрузке CSV.",
    }
    assert "labels" not in posts[0].json


def test_second_seed_skips_existing_and_posts_no_issues() -> None:
    mock = GitHubMock()
    client = _client(mock)
    seed_repositories(client, REPOS, SEED_ROOT)
    mock.calls.clear()
    output = io.StringIO()
    seed_repositories(client, REPOS, SEED_ROOT, output=output)
    assert _create_repo_posts(mock) == []
    assert _issue_posts(mock) == []
    text = output.getvalue()
    assert "Skipping acme/sales-dashboard: repository already exists." in text
    assert "Run tg-triage-drop, then seed, to recreate." in text
    assert "Skipping acme/crm:" in text
    assert "Skipping acme/customer-portal:" in text


def test_seed_does_not_write_fixture_files(tmp_path: Path) -> None:
    mock = GitHubMock()
    fixture_root = tmp_path / "fixtures" / "github"
    seed_repositories(_client(mock), REPOS, SEED_ROOT)
    assert not fixture_root.exists()


def test_refresh_writes_truncated_snapshot(tmp_path: Path) -> None:
    mock = GitHubMock()
    mock.existing.add("acme/sales-dashboard")
    mock.descriptions["acme/sales-dashboard"] = "дашборд продаж"
    mock.readmes["acme/sales-dashboard"] = "R" * 2500
    mock.issues["acme/sales-dashboard"] = [
        {
            "number": 99,
            "title": "PR not an issue",
            "body": "no",
            "pull_request": {},
        },
        *[
            {
                "number": index,
                "title": f"issue {index}",
                "body": "B" * 1500,
            }
            for index in range(12, 0, -1)
        ],
    ]
    fixture_root = tmp_path / "github"
    refresh_fixtures(
        _client(mock),
        (SALES,),
        fixture_root,
        k=10,
        readme_max_chars=2000,
        issue_body_max_chars=1000,
    )
    directory = fixture_root / "acme" / "sales-dashboard"
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"description": "дашборд продаж"}
    assert (directory / "README.md").read_text(encoding="utf-8") == "R" * 2000
    issues = json.loads((directory / "issues.json").read_text(encoding="utf-8"))
    assert len(issues) == 10
    assert issues[0]["number"] == 12
    assert issues[0]["body"] == "B" * 1000
    assert all("pull_request" not in item for item in issues)
    assert all(call.method == "GET" for call in mock.calls)
    assert _issue_posts(mock) == []
    assert _create_repo_posts(mock) == []
    assert _delete_calls(mock) == []


def test_refresh_after_list_does_not_post_issues(tmp_path: Path) -> None:
    mock = GitHubMock()
    mock.existing.update(str(repo) for repo in REPOS)
    for repo in REPOS:
        key = str(repo)
        mock.descriptions[key] = "d"
        mock.readmes[key] = "readme"
        mock.issues[key] = []
    refresh_fixtures(_client(mock), REPOS, tmp_path / "github", k=10)
    assert _issue_posts(mock) == []
    assert all(call.method == "GET" for call in mock.calls)


def test_drop_without_confirmation_sends_no_deletes() -> None:
    mock = GitHubMock()
    mock.existing.update(str(repo) for repo in REPOS)
    dropped = drop_repositories(
        _client(mock),
        REPOS,
        input_stream=io.StringIO("no\n"),
        output=io.StringIO(),
    )
    assert dropped is False
    assert _delete_calls(mock) == []


def test_drop_after_yes_deletes_only_configured_repos() -> None:
    mock = GitHubMock()
    mock.existing.update(str(repo) for repo in REPOS)
    mock.existing.add("acme/other")
    output = io.StringIO()
    dropped = drop_repositories(
        _client(mock),
        REPOS,
        input_stream=io.StringIO("yes\n"),
        output=output,
    )
    assert dropped is True
    deleted = [call.path for call in _delete_calls(mock)]
    assert deleted == [
        "/repos/acme/sales-dashboard",
        "/repos/acme/crm",
        "/repos/acme/customer-portal",
    ]
    listed = output.getvalue()
    assert "acme/sales-dashboard" in listed
    assert "acme/crm" in listed
    assert "acme/customer-portal" in listed
    assert "acme/other" not in listed


def test_drop_accepts_typed_repo_names() -> None:
    mock = GitHubMock()
    mock.existing.update(str(repo) for repo in REPOS)
    dropped = drop_repositories(
        _client(mock),
        REPOS,
        input_stream=io.StringIO(
            "acme/sales-dashboard acme/crm acme/customer-portal\n"
        ),
        output=io.StringIO(),
    )
    assert dropped is True
    assert len(_delete_calls(mock)) == 3
