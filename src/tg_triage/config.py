"""Load application configuration from environment variables and YAML.

Secrets never live in this module. Tests must pass an explicit env file and
must not rely on a repository-root `.env`. Runtime LLM HTTP timeouts are
module constants used by ``python -m tg_triage`` (local match can exceed 300s;
the read timeout is 900s).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tg_triage.domain import RepositoryId

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YAML_PATH = REPO_ROOT / "config" / "demo.yaml"

DEFAULT_LLM_BASE_URL = "http://localhost:11434/v1"
DEFAULT_LLM_MODEL = "qwen3:8b"
LLM_CONNECT_TIMEOUT = 10.0
LLM_READ_TIMEOUT = 900.0


class MissingSettingsError(Exception):
    """Required settings are absent for the requested entrypoint.

    ``names`` are the environment variable names that were missing or blank.
    """

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        super().__init__("Missing required settings: " + ", ".join(names))


class YamlConfig(BaseModel):
    """Closed-world demo list and snapshot truncation knobs."""

    repositories: tuple[RepositoryId, ...]
    k: int = 10
    readme_max_chars: int = 2000
    issue_body_max_chars: int = 1000

    @field_validator("repositories", mode="before")
    @classmethod
    def _parse_repositories(cls, value: object) -> tuple[RepositoryId, ...]:
        """Accept a YAML list of ``owner/repo`` strings.

        Raises:
            TypeError: If ``value`` is not a list.
            ValueError: If an entry is not ``owner/repo``.
        """
        if not isinstance(value, list):
            raise TypeError("repositories must be a list")
        return tuple(RepositoryId.parse(str(item)) for item in value)


class EnvSettings(BaseSettings):
    """Secrets and runtime paths from the environment.

    Does not auto-load a `.env` file. Callers pass ``env_file`` to
    :func:`load_settings` when they want dotenv values.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        env_file_encoding="utf-8",
        env_file=None,
    )

    telegram_bot_token: str | None = None
    telegram_group_chat_id: str | None = None
    telegram_owner_user_ids: str | None = None
    github_token: str | None = None
    github_owner: str | None = None
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str | None = None
    llm_model: str = DEFAULT_LLM_MODEL
    sqlite_path: str = "tg_triage.sqlite"

    @property
    def owner_user_ids(self) -> tuple[int, ...]:
        """Numeric owner ids parsed from a comma-separated env value."""
        raw = self.telegram_owner_user_ids
        if not raw:
            return ()
        return tuple(int(part.strip()) for part in raw.split(",") if part.strip())


class AppConfig(BaseModel):
    """Combined env + YAML configuration."""

    settings: EnvSettings
    yaml: YamlConfig

    @property
    def repositories(self) -> tuple[RepositoryId, ...]:
        """Closed-world repository list from YAML."""
        return self.yaml.repositories

    def require(self, *fields: str) -> None:
        """Refuse to proceed when named env settings are missing or blank.

        ``fields`` are ``EnvSettings`` attribute names. The error lists the
        corresponding environment variable names.

        Raises:
            MissingSettingsError: If any named field is ``None`` or empty.
        """
        missing: list[str] = []
        for field in fields:
            value = getattr(self.settings, field)
            if value is None or value == "":
                missing.append(field.upper())
        if missing:
            raise MissingSettingsError(tuple(missing))


def load_yaml_config(path: Path) -> YamlConfig:
    """Parse the demo YAML file into a typed closed-world config.

    Raises:
        ValueError: If the file is not a mapping.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid YAML at {path}")
    return YamlConfig.model_validate(data)


def load_settings(*, env_file: Path | None = None) -> EnvSettings:
    """Load secrets from process env, optionally overlaying a dotenv file.

    Process environment variables still apply. Passing ``env_file`` does not
    read a repository-root `.env` unless that path is given explicitly.
    """
    if env_file is None:
        return EnvSettings()
    return EnvSettings(_env_file=env_file, _env_file_encoding="utf-8")


def load_config(
    *,
    env_file: Path | None = None,
    yaml_path: Path | None = None,
) -> AppConfig:
    """Load env settings plus YAML. Defaults to ``config/demo.yaml``."""
    return AppConfig(
        settings=load_settings(env_file=env_file),
        yaml=load_yaml_config(yaml_path or DEFAULT_YAML_PATH),
    )
