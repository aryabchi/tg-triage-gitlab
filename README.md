# tg-triage

Python package: `tg_triage`.

## Purpose

`tg-triage` ingests free-form problem reports from a Telegram group, runs AI-assisted triage against a GitHub fixture snapshot, and lets the list owner edit one Markdown contract before any tracker write. After the owner uploads the file and confirms, the app creates a GitHub issue or records a skip. The LLM recommends; deterministic code validates and executes. See the [MVP System Specification](docs/mvp_system_specification.md).

## Automation workflow

1. Group text becomes a stored Problem.
2. Owner `/compile` produces a Markdown contract.
3. Owner edits the file outside Telegram and uploads it.
4. Owner `/execute` plus Confirm creates a GitHub issue or skips an existing one.

Operator scripts (after later implementation): `tg-triage-seed`, `tg-triage-refresh`, `tg-triage-drop`. They manage **fake demo repositories only**.

## Dependencies

- Python 3.13
- Runtime: `python-telegram-bot`, `httpx`, `pydantic`, `pydantic-settings`, `pyyaml`
- Stdlib: `sqlite3`
- Dev: `pytest`, `ruff`
- External systems (not Python packages): Telegram Bot API, GitHub Issues API, OpenRouter or host Ollama

Live tokens are optional for tests. Default `pytest` does not call Telegram, GitHub, or an LLM.

## Local setup

Windows (PowerShell), from the repository root. Live Telegram/GitHub/LLM are not required.

```text
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
python -c "import tg_triage; print(tg_triage.__file__)"
pytest -q
```

Leave secrets in `.env` blank for default tests. Tests use a temporary env file and do not read the real `.env`.
