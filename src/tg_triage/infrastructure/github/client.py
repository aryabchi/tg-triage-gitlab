"""Thin GitHub REST client for seed, refresh, drop, and issue create.

Paths and tokens stay here. Callers pass ``owner/repo`` identities from config.
"""

from __future__ import annotations

import base64
from typing import cast

import httpx

from tg_triage.domain import RepositoryId

API_VERSION = "2022-11-28"
USER_AGENT = "tg-triage"


def github_http_client(
    token: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Build a client for ``https://api.github.com`` with a bearer token.

    ``transport`` is for tests; production callers omit it.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    return httpx.Client(
        base_url="https://api.github.com",
        headers=headers,
        timeout=30.0,
        transport=transport,
    )


class GitHubClient:
    """REST helpers used by operator scripts and the issue-tracker adapter."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def get_repo(self, repository: RepositoryId) -> dict[str, object] | None:
        """Return the repository JSON, or ``None`` when GitHub responds 404."""
        owner, name = _split(repository)
        response = self._http.get(f"/repos/{owner}/{name}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _json_object(response)

    def create_repo(self, repository: RepositoryId, description: str) -> None:
        """Create ``owner/repo`` with a description and without an auto README.

        Tries the organization endpoint first, then the authenticated-user
        endpoint if GitHub reports that the owner is not an org.
        """
        owner, name = _split(repository)
        payload = {"name": name, "description": description, "auto_init": False}
        response = self._http.post(f"/orgs/{owner}/repos", json=payload)
        if response.status_code == 404:
            response = self._http.post("/user/repos", json=payload)
        response.raise_for_status()

    def set_description(self, repository: RepositoryId, description: str) -> None:
        """PATCH the repository description."""
        owner, name = _split(repository)
        response = self._http.patch(
            f"/repos/{owner}/{name}",
            json={"description": description},
        )
        response.raise_for_status()

    def put_readme(self, repository: RepositoryId, markdown: str) -> None:
        """Create ``README.md`` via the contents API. Does not update an existing file."""
        owner, name = _split(repository)
        encoded = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
        response = self._http.put(
            f"/repos/{owner}/{name}/contents/README.md",
            json={"message": "Add README", "content": encoded},
        )
        response.raise_for_status()

    def get_readme(self, repository: RepositoryId) -> str:
        """Return README text decoded from the contents API.

        Raises:
            httpx.HTTPStatusError: If the file is missing or the request fails.
        """
        owner, name = _split(repository)
        response = self._http.get(f"/repos/{owner}/{name}/contents/README.md")
        response.raise_for_status()
        data = _json_object(response)
        content = data.get("content")
        if not isinstance(content, str):
            raise ValueError("README contents response missing content")
        return base64.b64decode(content).decode("utf-8")

    def create_issue(self, repository: RepositoryId, title: str, body: str) -> dict[str, object]:
        """POST an issue with title and body only. No labels."""
        owner, name = _split(repository)
        response = self._http.post(
            f"/repos/{owner}/{name}/issues",
            json={"title": title, "body": body},
        )
        response.raise_for_status()
        return _json_object(response)

    def list_open_issues(self, repository: RepositoryId) -> list[dict[str, object]]:
        """Newest open issues that are not pull requests."""
        owner, name = _split(repository)
        response = self._http.get(
            f"/repos/{owner}/{name}/issues",
            params={
                "state": "open",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
            },
        )
        response.raise_for_status()
        items = _json_list(response)
        issues: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("issue entry must be an object")
            row = cast(dict[str, object], item)
            if "pull_request" in row:
                continue
            issues.append(row)
        return issues

    def delete_repo(self, repository: RepositoryId) -> None:
        """Delete ``owner/repo``. Callers must already have confirmed the list."""
        owner, name = _split(repository)
        response = self._http.delete(f"/repos/{owner}/{name}")
        response.raise_for_status()


def _split(repository: RepositoryId) -> tuple[str, str]:
    """Split a validated ``owner/repo`` identity."""
    owner, name = repository.value.split("/", 1)
    return owner, name


def _json_object(response: httpx.Response) -> dict[str, object]:
    """Parse a JSON object body.

    Raises:
        ValueError: If the body is not an object.
    """
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return cast(dict[str, object], data)


def _json_list(response: httpx.Response) -> list[object]:
    """Parse a JSON array body.

    Raises:
        ValueError: If the body is not a list.
    """
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("expected a JSON array")
    return cast(list[object], data)
