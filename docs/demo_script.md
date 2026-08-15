# Demo script

Walk Scenario A, then Scenario B. Fake encoding: `tests/e2e/test_fake_workflow.py` (default `pytest`). Live walk: operator + list owner on Telegram, host Ollama, and seeded GitHub repos. Exact group wording is Russian. Do not commit `.env`.

Live BotFather, group, seed/refresh/drop, and Ollama: [operator_setup.md](operator_setup.md).

## Shared setup (before either scenario)

Live `SINCE` is **today’s date** (`YYYY-MM-DD`). Fake tests use `2026-08-13`.

| Step | What to do | Who | Expected |
|---|---|---|---|
| 0.1 | Fill `.env`: `GITHUB_TOKEN`, `GITHUB_OWNER`, Telegram keys. LLM defaults to host Ollama `qwen3:8b`. | Operator | Process can authenticate; no secrets in git |
| 0.2 | `tg-triage-seed` then `tg-triage-refresh` | Operator | Three **new fake** repos exist under `GITHUB_OWNER`; `fixtures/github/<owner>/sales-dashboard/issues.json` contains the Russian export issue (number `#N`) |
| 0.3 | `ollama serve` (model `qwen3:8b` pulled) and `python -m tg_triage` | Operator | Console logs the LLM URL/model and `Telegram polling started` |
| 0.4 | Use today’s date as `SINCE` (`YYYY-MM-DD`) | List Owner | `/compile` will include the messages posted in this session |

Repos in play (made-up names only): `{GITHUB_OWNER}/sales-dashboard`, `{GITHUB_OWNER}/crm`, `{GITHUB_OWNER}/customer-portal`.

## Scenario A — Cluster + create + uncertain (human resolves one)

Goal: two similar export reports become one Create; a vague sync report is Uncertain; owner resolves Uncertain to Create on `crm`.

| Step | What to do | Who | Expected |
|---|---|---|---|
| A1 | In the reporter **group**, send: `Экспорт дашборда продаж больше не работает. Крутится загрузка.` | Reporter | New `Problem`, `lifecycle=ingested`, original text preserved |
| A2 | In the group, send: `На дашборде продаж экспорт зависает на спиннере. Нужен отчёт к планерке.` | Reporter | Second ingested Problem (different message id) |
| A3 | In the group, send: `Синхронизация клиентов задерживается.` | Reporter | Third ingested Problem |
| A4 | In a **DM** with the bot: `/compile SINCE` | List Owner | Bot sends unparsed `triage-run-<id>.md`. File has `## Legend`. Export reports share one `###` item under `## Create` (two Problem ids) **or** under `## Skip` if the model matched seed issue `#N` — if Skip, owner keeps the demo’s Create story by moving that block to `## Create` only when they judge it a new issue; prefer showing **clustering** (two ids, one item). Message A3 is under `## Uncertain` (`Repo: unknown`). Priority may be P1 on export. Compile can take several minutes on local Ollama; watch the console. |
| A5 | Download the `.md`, edit outside Telegram: keep clustered export as Create on `sales-dashboard`; move the Uncertain `###` into `## Create`; set `Repo: {GITHUB_OWNER}/crm`; add a `Body`. Save. | List Owner | Edited file is the authority; generated file is stale |
| A6 | Upload the edited `.md` in the same DM | List Owner | Run status `awaiting_execute`; bot stored bytes. If Telegram times out, resend the same file. |
| A7 | `/execute` then tap **Confirm** (not Cancel) | List Owner | Two GitHub issues created (export + crm). Bot replies with two URLs. Seeded export issue is **not** commented on. SQLite: three Problems `linked`. |

## Scenario B — Skip existing + leave uncertain

Goal: a later export report Skip-matches an existing issue; a vague portal-vs-CRM report stays Uncertain and is **not** executed.

| Step | What to do | Who | Expected |
|---|---|---|---|
| B0 | After Scenario A (or using seed issue `#N`), run `tg-triage-refresh` | Operator | Snapshot contains the export issue to match. Already-`linked` Problems will not re-enter compile |
| B1 | In the group, send: `CSV-экспорт дашборда всё ещё висит — как раньше.` | Reporter | New ingested Problem (not the A1–A3 rows) |
| B2 | In the group, send: `Что-то не так с клиентами, не понятно: портал или CRM.` | Reporter | New ingested Problem |
| B3 | DM: `/compile SINCE` | List Owner | Item B1 → `## Skip` with `Existing: {GITHUB_OWNER}/sales-dashboard#N`. Item B2 → `## Uncertain` |
| B4 | Edit: **leave** Uncertain in `## Uncertain`. Keep Skip. Save and upload in DM | List Owner | Upload stored; Uncertain still not a Create |
| B5 | `/execute` then **Confirm** | List Owner | **Zero** new GitHub creates. Skip recorded with `#N`. Problem B1 `linked` to that `IssueRef`. Problem B2 still `ingested`. Bot lists Skipped; no new URL for B2. A later `/compile` can pick up B2 again |

Together the tables show clustering, matching, uncertainty, Markdown authority, confirm gate, create, skip-without-mutate, and lifecycle.

## Gate C (after the live walk)

- Several free-form group reports (Russian)
- Problems in SQLite
- Owner `/compile` in DM
- Output shows clustered reports, a skip-able existing issue, a likely repo, a priority, at least one **Uncertain**
- Owner edits the file (resolve or leave uncertain; create vs skip)
- Upload + `/execute` + Confirm
- Issues created; skips recorded with refs/URLs
- LLM did not create issues by itself
