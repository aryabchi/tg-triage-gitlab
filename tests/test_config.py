"""Config loads from a temp env file and YAML. Never reads the repo `.env`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tg_triage.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    MissingSettingsError,
    RepositoryId,
    load_config,
)

DEMO_YAML = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"

_ENV_PREFIXES = ("TELEGRAM_", "GITHUB_", "LLM_", "SQLITE_")


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def temp_env(tmp_path: Path, isolated_env: None) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    return env_file


def test_llm_defaults(temp_env: Path) -> None:
    config = load_config(env_file=temp_env, yaml_path=DEMO_YAML)
    assert config.settings.llm_model == DEFAULT_LLM_MODEL
    assert config.settings.llm_model == "openai/gpt-oss-20b:free"
    assert config.settings.llm_base_url == DEFAULT_LLM_BASE_URL
    assert config.settings.llm_base_url == "https://openrouter.ai/api/v1"


def test_demo_yaml_repositories(temp_env: Path) -> None:
    config = load_config(env_file=temp_env, yaml_path=DEMO_YAML)
    assert config.repositories == (
        RepositoryId("acme/sales-dashboard"),
        RepositoryId("acme/crm"),
        RepositoryId("acme/customer-portal"),
    )
    assert config.yaml.k == 10
    assert config.yaml.readme_max_chars == 2000
    assert config.yaml.issue_body_max_chars == 1000


def test_temp_env_overrides_llm(tmp_path: Path, isolated_env: None) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_MODEL=qwen3:8b\nLLM_BASE_URL=http://localhost:11434/v1\n",
        encoding="utf-8",
    )
    config = load_config(env_file=env_file, yaml_path=DEMO_YAML)
    assert config.settings.llm_model == "qwen3:8b"
    assert config.settings.llm_base_url == "http://localhost:11434/v1"


def test_never_reads_repo_dotenv(temp_env: Path) -> None:
    config = load_config(env_file=temp_env, yaml_path=DEMO_YAML)
    assert config.settings.telegram_bot_token is None
    assert config.settings.github_token is None


def test_require_missing_bot_settings(temp_env: Path) -> None:
    config = load_config(env_file=temp_env, yaml_path=DEMO_YAML)
    with pytest.raises(MissingSettingsError) as exc_info:
        config.require("telegram_bot_token", "telegram_group_chat_id", "telegram_owner_user_ids")
    assert exc_info.value.names == (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_GROUP_CHAT_ID",
        "TELEGRAM_OWNER_USER_IDS",
    )


def test_require_passes_when_present(tmp_path: Path, isolated_env: None) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "TELEGRAM_GROUP_CHAT_ID=-100123\n"
        "TELEGRAM_OWNER_USER_IDS=1, 2\n",
        encoding="utf-8",
    )
    config = load_config(env_file=env_file, yaml_path=DEMO_YAML)
    config.require("telegram_bot_token", "telegram_group_chat_id", "telegram_owner_user_ids")
    assert config.settings.owner_user_ids == (1, 2)
