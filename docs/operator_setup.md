# Operator setup

Live BotFather, group, GitHub demo repos, and host Ollama. Default `pytest` does not need this. Do not commit `.env`.

## Telegram bot and group

1. Talk to [@BotFather](https://t.me/BotFather). Create a bot. Copy `TELEGRAM_BOT_TOKEN`.
2. Create one reporter group. Add the bot.
3. Disable **Group Privacy** (`/setprivacy` → Disable) **or** make the bot a group admin so ordinary text is visible. The app does not create the group.
4. Send a message in the group, then open `https://api.telegram.org/bot<token>/getUpdates`. Read `chat.id`. For a supergroup it is negative (`-100…`). Put that value in `TELEGRAM_GROUP_CHAT_ID` **with the minus sign**.
5. Your numeric user id goes in `TELEGRAM_OWNER_USER_IDS` (comma-separated if several). Only those ids may `/compile`, upload, `/execute`, Confirm, or Cancel.

## Environment

Copy `.env.example` to `.env` and fill secrets. Keys:

| Key | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_GROUP_CHAT_ID` | Reporter group id (include `-` for supergroups) |
| `TELEGRAM_OWNER_USER_IDS` | Owner Telegram user ids |
| `GITHUB_TOKEN` | PAT for seed/refresh/drop and Confirm creates |
| `GITHUB_OWNER` | Live GitHub user/org; overlays YAML `acme/` |
| `LLM_BASE_URL` | Default `http://localhost:11434/v1` (Ollama) |
| `LLM_API_KEY` | Unused for Ollama; OpenRouter key if you switch |
| `LLM_MODEL` | Default `gpt-oss:20b-cloud` - fast + [Free Cloud usage](https://ollama.com/settings`), then `qwen3:8b` - local & slow |
| `SQLITE_PATH` | Local DB file (gitignored) |

Restart `python -m tg_triage` after any `.env` change.

## LLM (Ollama default)

Host Ollama must be running before `/compile`.

```text
ollama pull qwen3:8b
ollama serve
```

Leave `LLM_API_KEY` empty. Compile cluster then match; match can take several minutes on CPU. The console logs each stage so a long wait is visible. LLM HTTP read timeout is 900 seconds; Telegram Bot API connect timeout is 30 seconds.

To use OpenRouter instead, set `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_MODEL=openai/gpt-oss-20b:free`, and `LLM_API_KEY`. There is no in-process router.

## Fake GitHub demo repos

Scripts touch **only** the closed-world list (`sales-dashboard`, `crm`, `customer-portal`) under `GITHUB_OWNER`. Seed is create-once.

```text
tg-triage-seed
tg-triage-refresh
```

If a repo already exists, seed prints a skip message and does not add issues. To rebuild: `tg-triage-drop` (type `yes` or the full `owner/repo` list; there is no `--yes`), then seed, then refresh. Live fixture files under `fixtures/github/<owner>/` are gitignored.

## Run the bot

From the repo root, venv active, `.env` filled:

```text
python -m tg_triage
```

Console should log the LLM URL/model and `Telegram polling started`.

Smoke (Cancel is enough; Confirm writes GitHub):

1. Group: `SMOKE кнопка экспорта ничего не делает`
2. Owner DM: `/compile YYYY-MM-DD`
3. Download `triage-run-<id>.md`, re-upload it, `/execute`, Cancel

If compile fails, the DM is `Compile failed. No document sent.` If an upload times out, resend the same file. A new `/compile` supersedes leftover pending/awaiting runs.
