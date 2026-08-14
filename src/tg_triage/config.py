"""Load application configuration from environment variables and YAML.

Secrets never live in this module. Tests must pass an explicit env file and
must not rely on a repository-root `.env`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YAML_PATH = REPO_ROOT / "config" / "demo.yaml"

DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_MODEL = "openai/gpt-oss-20b:free"


@dataclass(frozen=True, slots=True)
class RepositoryId:
    """Closed-world tracker identity. MVP form is ``owner/repo``."""

    value: str

    def __str__(self) -> str:
        return self.value


class MissingSettingsError(Exception):
    """Required settings are absent for the requested entrypoint."""

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
        if not isinstance(value, list):
            raise TypeError("repositories must be a list")
        return tuple(RepositoryId(str(item)) for item in value)


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
        return self.yaml.repositories

    def require(self, *fields: str) -> None:
        """Refuse to proceed when named env settings are missing or blank."""
        missing: list[str] = []
        for field in fields:
            value = getattr(self.settings, field)
            if value is None or value == "":
                missing.append(field.upper())
        if missing:
            raise MissingSettingsError(tuple(missing))


def load_yaml_config(path: Path) -> YamlConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid YAML at {path}")
    return YamlConfig.model_validate(data)


def load_settings(*, env_file: Path | None = None) -> EnvSettings:
    if env_file is None:
        return EnvSettings()
    return EnvSettings(_env_file=env_file, _env_file_encoding="utf-8")


def load_config(
    *,
    env_file: Path | None = None,
    yaml_path: Path | None = None,
) -> AppConfig:
    return AppConfig(
        settings=load_settings(env_file=env_file),
        yaml=load_yaml_config(yaml_path or DEFAULT_YAML_PATH),
    )
