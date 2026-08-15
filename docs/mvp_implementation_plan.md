# MVP implementation plan

Greenfield Python project. Spec and architecture are locked: [MVP System Specification](mvp_system_specification.md), [MVP Architecture](mvp_architecture.md). Follow [implementation rules](../.cursor/rules/implementation.mdc): Python 3.13, `pyproject.toml`, dedicated venv, type hints, no unnecessary `Any`, ports for every external system, secrets only via env.

A greenfield Python 3.13 bot that ingests Telegram group problems, compiles a validated Markdown contract via a two-stage LLM workflow against GitHub fixtures, then creates or skips GitHub issues only after the owner uploads and confirms. Every step is independently testable with fakes; live Telegram/GitHub/LLM appear only as explicit smoke steps.

Do not implement GitLab, RAG, labels-on-create, comments on existing issues, webhooks, or an agent loop.

**Fake tracker world.** Seed, refresh, drop, and fixtures use only **made-up** repositories invented for this demo (`sales-dashboard`, `crm`, `customer-portal` under a configurable owner). Do not clone, fork, snapshot, or attach to any pre-existing GitHub repository. Live GitHub is used only to **create those new fake repos** under the operator’s `GITHUB_OWNER` (see [Open questions](#8-open-questions)).

**Language.** Workflow field names in the Markdown contract stay as locked in [MVP Architecture, Markdown Contract](mvp_architecture.md#markdown-contract-renderer--parser) (`Repo`, `Problems`, `Body`, …). User-facing content in seed `meta` / README / issues, test fixtures, canned problem texts, and demo Telegram messages is **Russian** where it is prose (titles, bodies, descriptions).

---

## 1. Implementation strategy

Build **inside-out**, one verifiable slice at a time. Domain and application services are sync and port-driven. Adapters (Telegram, GitHub, LLM HTTP, SQLite) sit at the edge and are fakeable.

**First vertical slice that proves the architecture (no live APIs):** fake Telegram event → persist `Problem` → canned LLM JSON → validated Markdown → parse edited upload → Confirm → fake create/skip → `Problem.lifecycle = linked`.

**Control principle, enforced by construction:** LLM recommends structured JSON → application validates → deterministic code renders Markdown, parses the owner file, and performs GitHub writes. The LLM never receives a GitHub token and never authors the contract file.

**Locked implementation choices** (architecture deferred library choice in [MVP Architecture, Remaining architectural questions](mvp_architecture.md#13-remaining-architectural-questions); this plan locks it):

- Package: `tg_triage` under `src/`
- Telegram library: `python-telegram-bot` v21+ (long polling, `Application`) — rationale below
- HTTP: `httpx` (GitHub + OpenAI-compatible LLM)
- LLM DTOs / config: `pydantic` v2 + `pydantic-settings`
- Tests: `pytest` (+ `pytest-asyncio` only for Telegram adapter tests)
- DB: stdlib `sqlite3`, one file (tests use a temp file or `:memory:`)
- App services stay **sync**; the Telegram adapter is the async shell

### Telegram library: `python-telegram-bot` vs `aiogram`

Architecture left the Bot API library to implementation ([MVP Architecture, Remaining architectural questions](mvp_architecture.md#13-remaining-architectural-questions)). Both wrap the same Telegram Bot API and long polling. The choice does not change ports, domain, or Option A.

| | `python-telegram-bot` v21+ | `aiogram` 3.x |
|---|---|---|
| Fit to this MVP | Thin `Application` + handlers around a sync orchestrator | Full async framework with routers and an FSM we would not use |
| Polling | First-class `run_polling`; matches [MVP Architecture, Key architectural decisions](mvp_architecture.md#12-key-architectural-decisions) (single process, no webhook) | Also supports polling; more ceremony to stay “just handlers” |
| Documents / no parse_mode | Direct `send_document` for unparsed `.md` (Option A in [MVP System Specification, Telegram interaction model](mvp_system_specification.md#7-telegram-interaction-model)) | Equivalent, slightly more boilerplate |
| Testing | Handlers can be called with synthetic updates; we still fake `TelegramGateway` | Same idea; FSM extras unused |
| Cost | v21 is async-first, so handlers `asyncio.to_thread` into sync services | Native async; would push the application layer async for little gain |
| Ecosystem | Very common for “small official-style bots”; stable Handler API | Very common in Russian-language tutorials; heavier than this bot |

**Choice: `python-telegram-bot` v21+.** This bot is a thin adapter over a deterministic orchestrator, not a conversational FSM product. `python-telegram-bot` maps onto “poll, dispatch, sendDocument, inline Confirm/Cancel” with less framework surface. `aiogram`’s FSM and router model would be unused complexity. The async tax (`to_thread` around sync services) is acceptable and keeps application tests synchronous.

### LLM integration (initial implementation — locked here)

Isolation:

- Port: `LlmJudgment` with two methods, `cluster(problems) -> ClusterResult` and `match(items, evidence_pack) -> MatchResult`
- Runtime adapter: one OpenAI-compatible HTTP client (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`)
- Tests: `FakeLlmJudgment` returning canned JSON (no network)
- The triage orchestrator never imports `httpx` or provider SDKs
- No tools, no Telegram/GitHub types in the LLM package

Where prompts live (versioned in git, not in DB, not inlined in the HTTP client):

- [src/tg_triage/llm/prompts/cluster.md](../src/tg_triage/llm/prompts/cluster.md) — system+user template: ids + original texts only; return JSON
- [src/tg_triage/llm/prompts/match.md](../src/tg_triage/llm/prompts/match.md) — clustered items + serialized `EvidencePack`; instruct: only identities in the pack/config, else `uncertain`
- [src/tg_triage/llm/schemas/cluster.json](../src/tg_triage/llm/schemas/cluster.json)
- [src/tg_triage/llm/schemas/match.json](../src/tg_triage/llm/schemas/match.json)

JSON contracts:

- Cluster: `{ "items": [ { "summary": string, "problem_ids": [int, ...] } ] }`
- Match: `{ "items": [ { "problem_ids": [int], "outcome": "create_new"|"link_existing"|"uncertain", "repository": "owner/repo"|"unknown", "existing": "owner/repo#n"|null, "priority": "P1"|"P2"|"P3"|null, "title": string, "body": string, "rationale": string } ] }`

Validation (application, not the model):

1. Parse JSON; reject non-JSON / schema mismatch
2. Cluster: every in-period Problem id appears in **exactly one** item (partition)
3. Match: `repository` in configured list or `unknown`; `link_existing` requires `existing` whose number exists in that repo’s **snapshot**; `uncertain` must not invent a repo; `create_new` must have title+body and a known repo
4. On failure: **one retry** (same prompt + a short “return valid JSON matching schema” suffix), then `TriageRun.status = failed`
5. Debug write of raw request/response under `debug/triage-runs/<run-id>/` (gitignore). Failed debug write must **not** fail compile. `/execute` must not read this directory

Invalid / uncertain handling:

- Schema/coverage/identity failure → retry once → fail the run (owner notified; no document sent)
- Semantic uncertainty → `outcome = uncertain`, rendered under `## Uncertain`, not executed as create
- Owner may move a `###` block between `## Create` / `## Skip` / `## Uncertain`, or delete it (exclude; Problems stay `ingested`)

**Provider order (config, not a router):** default is host Ollama `qwen3:8b` (`LLM_BASE_URL=http://localhost:11434/v1`, unused `LLM_API_KEY`). OpenRouter `openai/gpt-oss-20b:free` remains a config swap (`LLM_BASE_URL=https://openrouter.ai/api/v1` plus `LLM_API_KEY`) if the host model is unavailable. No in-process model router. Defaults and when tokens are required: [Open questions](#8-open-questions).

**Minimum useful test level per major component**

- Domain types / coverage / lifecycle: **unit**
- SQLite repositories: **integration** (temp SQLite file)
- Problem intake: **unit** (fake repo)
- Markdown renderer/parser: **unit** + golden files
- Execution service: **unit** (fake `IssueTracker`)
- Fixture `KnowledgeSource`: **unit** (files under `tests/fixtures/`)
- LLM HTTP client: **unit** (httpx mock) — do not call live LLM in CI
- Triage orchestrator: **integration** (fake LLM + real validator/renderer + temp SQLite)
- Application orchestrator (run state): **integration** (all ports faked)
- GitHub adapter: **unit** (mocked HTTP); **smoke** live create ([Step 13](#step-13--live-github-smoke-operator))
- Seed / refresh / drop scripts: **unit** (mocked HTTP); **smoke** live seed+refresh ([Step 13](#step-13--live-github-smoke-operator)); drop is destructive and opt-in
- Telegram adapter: **unit** (no network; fake `TelegramGateway` / recorded bot methods); **smoke** live polling ([Step 15](#step-15--live-telegram-smoke-operator))
- Full workflow: **end-to-end with fakes** in CI; **real E2E** only for the two demo scenarios in [Two concrete real end-to-end demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios)

---

## 2. Dependency / order rationale

Order is risk-first and demo-path, not package-taxonomy.

1. Skeleton + domain + SQLite + intake — accumulation is the source of truth; everything else selects from it
2. Markdown contract next — it is the HITL/execute authority; parser bugs are cheaper to find before LLM or Telegram exist
3. Execute against a fake tracker — proves validate-all-then-write, confirm gate, and idempotency without GitHub
4. Fixtures loader — compile must fail closed on missing files; needed before triage
5. LLM port + validators + triage orchestrator — semantic stage, still fully fakeable
6. Application orchestrator — wires compile/upload/execute/supersede without Telegram
7. Fake E2E — proves the architecture before any live API
8. GitHub adapter + seed / refresh / drop (mocked, then live smoke) — tracker writes and a disposable fake demo world
9. Telegram adapter (fakes, then live smoke) — last runtime I/O; Option A round-trip
10. Live LLM compile smoke + full demo rehearsal — only after the workflow is deterministic

Telegram last: polling and chat IDs do not change domain behavior. GitHub seed before live compile: fixtures must contain the skip-match issue. Live LLM after canned-JSON tests: otherwise schema/coverage bugs are blamed on the model.

---

## 3–4. Numbered implementation steps

(Verification and Definition of Done are included in each step.)

### Step 1 — Project skeleton and configuration

**Purpose.** Installable Python 3.13 package, venv, test runner, env-based secrets, closed-world **fake** repo list, and a concise root README for local development.

**Create/modify:**

- [README.md](../README.md) — developer-facing, concise (see outline below). Not [docs/operator_setup.md](operator_setup.md) (that is live BotFather/group steps in [Step 15](#step-15--live-telegram-smoke-operator)).
- [pyproject.toml](../pyproject.toml) — `requires-python = ">=3.13,<3.14"`; deps: `python-telegram-bot`, `httpx`, `pydantic`, `pydantic-settings`; dev: `pytest`, `ruff`
- [src/tg_triage/__init__.py](../src/tg_triage/__init__.py), [src/tg_triage/config.py](../src/tg_triage/config.py)
- [config/demo.yaml](../config/demo.yaml) — `repositories: [acme/sales-dashboard, acme/crm, acme/customer-portal]` as the **fictional** closed list used in tests; live overlay replaces `acme` with `GITHUB_OWNER` (see [Open questions](#8-open-questions)). `k: 10`, `readme_max_chars: 2000`, `issue_body_max_chars: 1000`
- [.env.example](../.env.example) — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `TELEGRAM_OWNER_USER_IDS`, `GITHUB_TOKEN`, `GITHUB_OWNER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `SQLITE_PATH` (placeholders only; no real secrets)
- [.gitignore](../.gitignore) — already ignores `.env`, `*.sqlite*`, `debug/`; keep that
- [tests/test_config.py](../tests/test_config.py)

**README.md required sections** (keep short; link out rather than duplicate spec):

1. **App name** — `tg-triage` (Python package `tg_triage`).
2. **App purpose** — one paragraph: ingest free-form Telegram group problems, AI-assisted triage against a GitHub fixture snapshot, owner edits one Markdown contract, then deterministic GitHub issue create or skip. LLM recommends; code executes. Point to [MVP System Specification](mvp_system_specification.md).
3. **Automation workflow** — the four stages from [MVP System Specification, System boundaries](mvp_system_specification.md#6-system-boundaries): group text → Problem; owner `/compile` → Markdown; owner edit + upload; `/execute` + Confirm → create or skip. Mention operator scripts that will exist after [Step 12](#step-12--github-adapters-mocked-http--seed--refresh--drop-scripts): `tg-triage-seed`, `tg-triage-refresh`, `tg-triage-drop` (fake demo repos only).
4. **Dependencies** — Python 3.13; runtime: `python-telegram-bot`, `httpx`, `pydantic`, `pydantic-settings`; stdlib `sqlite3`; dev: `pytest`, `ruff`. External systems (not installed as packages): Telegram Bot API, GitHub Issues API, OpenRouter or host Ollama. Live tokens are optional for tests.
5. **How to setup the dev environment locally** — Windows-friendly commands: `python -m venv .venv`, activate, `pip install -e ".[dev]"`, copy `.env.example` to `.env` (leave secrets blank for default tests), `pytest -q`. Do not require live Telegram/GitHub/LLM for this setup.

**Functionality.** Load config from env + yaml. Refuse to start if required keys missing when a given entrypoint is used. No bot, no DB schema yet. Tests never read the real `.env`.

**Verification.**

```text
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -c "import tg_triage; print(tg_triage.__file__)"
pytest tests/test_config.py -q
```

Use a temp env in the test. Assert default `LLM_MODEL` is `qwen3:8b` and default `LLM_BASE_URL` is `http://localhost:11434/v1`. Assert `config/demo.yaml` parses to three `RepositoryId` values (`acme/sales-dashboard`, `acme/crm`, `acme/customer-portal`).

README check (no extra test file required): `README.md` exists at repo root and contains headings (or equivalent titled sections) for name, purpose, workflow, dependencies, and local setup. Setup commands in the README match the verification block above.

**Expected.** Package imports; config tests pass; no network; README is usable as the local onboarding page.

**Done.** `pip install -e ".[dev]"` works on 3.13; config loads from env+yaml; secrets are not hardcoded; `.env.example` lists every key; [README.md](../README.md) has the five sections above.

---

### Step 2 — Domain types and invariants

**Purpose.** Tracker-neutral domain with no Telegram/GitHub types.

**Create:** [src/tg_triage/domain/](../src/tg_triage/domain/) — `Problem`, `ProblemLifecycle` (`ingested`|`linked`), `TriageRun`, `TriageRunStatus`, `TriageItem`, `TriageOutcome`, `RepositoryId`, `IssueRef`, `EvidencePack` / `EvidenceSnippet`, `ExecutionResult`, `MarkdownPlan`. Coverage helper: `assert_coverage(problem_ids, items)`.

**Tests:** [tests/unit/test_domain.py](../tests/unit/test_domain.py)

**Functionality.** Value objects and invariants only. `IssueRef.parse("owner/repo#81")`. `RepositoryId` rejects empty / non `owner/repo` form. Coverage fails on missing, duplicate, or extra ids.

**Verification.** `pytest tests/unit/test_domain.py -q`

Cases: partition success; missing id; duplicate id; `IssueRef` round-trip; lifecycle has exactly two values.

**Expected.** All pass; `grep` of `src/tg_triage/domain` finds no `telegram`, `github.com`, or `http`.

**Done.** Domain compiles independently; invariants have failing-then-passing unit tests; no infrastructure imports.

---

### Step 3 — Persistence ports and SQLite

**Purpose.** Single local store for Problems, TriageRuns (generated/uploaded markdown, items JSON, status, execution result).

**Create:**

- Ports: [src/tg_triage/ports/repositories.py](../src/tg_triage/ports/repositories.py)
- Adapter: [src/tg_triage/infrastructure/sqlite.py](../src/tg_triage/infrastructure/sqlite.py) — schema + `ProblemRepository` + `TriageRunRepository`
- Tests: [tests/integration/test_sqlite.py](../tests/integration/test_sqlite.py)

**Functionality.** Unique `(telegram_chat_id, telegram_message_id)`. Query `created_at >= since` AND `lifecycle = ingested`. Store/load markdown bytes on the run. Status update `pending` → `superseded`. Persist `execution_result` JSON.

**Verification.** `pytest tests/integration/test_sqlite.py -q` against a temp file (not a server DB).

Cases: insert Problem; duplicate message id is idempotent (same row); date+lifecycle filter excludes `linked` and too-old rows; round-trip upload bytes; transactional status change.

**Expected.** Tests pass with no PostgreSQL/Docker.

**Done.** SQLite is the system of record; tests never require a live database server.

---

### Step 4 — Problem intake use case

**Purpose.** Eligible group text becomes a `Problem`. No LLM. Idempotent.

**Create:** [src/tg_triage/application/intake.py](../src/tg_triage/application/intake.py), [tests/unit/test_intake.py](../tests/unit/test_intake.py), [tests/fakes/](../tests/fakes/) `FakeProblemRepository`.

**Functionality.** `ingest_group_text(text, user_id, message_id, chat_id, created_at)` → `lifecycle=ingested`, original text preserved. Second call with same `(chat_id, message_id)` returns the existing row, does not duplicate. Intake does **not** filter commands; the Telegram adapter will not call it for commands/documents. Keep intake stupid.

**Verification.** `pytest tests/unit/test_intake.py -q`

Sample input: `"Экспорт дашборда продаж больше не работает.\nКрутится загрузка."` plus ids. Assert `original_text` exact match; `lifecycle == ingested`; linked_issue is None; duplicate message id → one row.

**Expected.** Unit tests pass with fake repo (SQLite optional extra case may reuse [Step 3](#step-3--persistence-ports-and-sqlite)).

**Done.** Intake is deterministic, idempotent, and has no Telegram import.

---

### Step 5 — Markdown contract (render + parse)

**Purpose.** One schema both ways. Section heading **is** the action. `## Legend` skipped. Frozen example from [MVP Architecture, Markdown Contract](mvp_architecture.md#markdown-contract-renderer--parser) is the **syntax** golden file (field names and that example’s wording stay as locked there).

**Create:**

- [src/tg_triage/markdown/renderer.py](../src/tg_triage/markdown/renderer.py)
- [src/tg_triage/markdown/parser.py](../src/tg_triage/markdown/parser.py)
- [src/tg_triage/markdown/validate_plan.py](../src/tg_triage/markdown/validate_plan.py)
- [tests/golden/contract_example.md](../tests/golden/contract_example.md) — copy the architecture frozen example (run 7, three sections)
- [tests/unit/test_markdown.py](../tests/unit/test_markdown.py)

**Functionality.**

- Render `TriageItem[]` → file starting with `# GitHub Issues`, `# run:`, `# since:`, `## Legend`, then `## Create` / `## Skip` / `## Uncertain` (omit empty sections)
- Parse → `MarkdownPlan` with items tagged by section
- Validate: unknown `##` fails the whole plan; Create requires Repo (not `unknown`), Problems, Body; Skip requires Repo, Existing `owner/repo#n`, Problems; Uncertain requires Problems, Reason; repos must be in config (except Uncertain may be `unknown`); Priority never fails the plan; unknown action/section refused
- Deleted `###` blocks = exclude (not present in plan; Problems stay ingested at execute)

**Verification.** `pytest tests/unit/test_markdown.py -q`

Cases:

1. Parse golden file → 1 create (`acme/sales-dashboard`, problems 101/102), 1 skip (`acme/sales-dashboard#81`, problem 104), 1 uncertain (problem 103)
2. Render those items → parse again equals original plan (Legend may differ only in whitespace)
3. Unknown `## Foo` → validation error
4. Create with `Repo: unknown` → error
5. Skip without `Existing` → error
6. Owner moves the uncertain `###` into `## Create` with a valid repo and Body → parse succeeds as create

**Expected.** Golden parse matches [MVP Architecture, Markdown Contract](mvp_architecture.md#markdown-contract-renderer--parser); round-trip stable; invalid files fail closed.

**Done.** Renderer is canonical (LLM does not write this file). Parser is the only execute input path.

---

### Step 6 — Execution service against a fake IssueTracker

**Purpose.** Validate-all-then-write; confirm gate; persist URLs; no duplicate creates on retry. Uncertain/exclude do nothing.

**Create:**

- Port: [src/tg_triage/ports/issue_tracker.py](../src/tg_triage/ports/issue_tracker.py) — `create(repository, title, body) -> IssueRef + url`
- [src/tg_triage/application/execute.py](../src/tg_triage/application/execute.py)
- [tests/fakes/fake_issue_tracker.py](../tests/fakes/fake_issue_tracker.py)
- [tests/unit/test_execute.py](../tests/unit/test_execute.py)

**Functionality.**

- `preview(uploaded_markdown)` → parse+validate; **zero** tracker calls
- `execute_confirmed(run)` → for each Create: tracker.create(title, body only; **ignore Priority**); persist URL on that item immediately; mark those Problems `linked` + `linked_issue`; for each Skip: record Existing, **no** tracker call, Problems `linked`; Uncertain: no write, Problems stay `ingested`
- Re-execute: skip Create items that already have a stored created URL
- Cancel: `TriageRun.status = cancelled`; no writes
- Partial failure: keep succeeded URLs; remaining Creates still eligible

**Verification.** `pytest tests/unit/test_execute.py -q`

Use the architecture golden markdown ([Step 5](#step-5--markdown-contract-render--parse)) + FakeIssueTracker that records calls. Assert: preview call count 0; after confirm, one `create` with title `Sales dashboard export hangs` and body containing the two-user summary; skip item → 0 extra creates; problems 101/102/104 `linked`; 103 still `ingested`; second execute → still one create total; invalid markdown → no creates.

**Expected.** Fake tracker is the only “GitHub”; tests pass offline.

**Done.** Execution never talks to LLM. Confirm is mandatory in the API (`execute_confirmed` vs `preview`).

---

### Step 7 — Fixture knowledge source

**Purpose.** `/compile` reads files only. Missing fixtures fail the run. Fixtures are **invented** demo snapshots, not dumps of real GitHub projects.

**Create:**

- Port: [src/tg_triage/ports/knowledge.py](../src/tg_triage/ports/knowledge.py)
- [src/tg_triage/infrastructure/fixtures.py](../src/tg_triage/infrastructure/fixtures.py)
- Sample test fixtures (Russian prose): `tests/fixtures/github/acme/sales-dashboard/{meta.json, README.md, issues.json}` (and `crm`, `customer-portal`)
- [tests/unit/test_fixture_source.py](../tests/unit/test_fixture_source.py)

Layout matches [MVP Architecture, Key architectural decisions](mvp_architecture.md#12-key-architectural-decisions): `fixtures/github/<owner>/<repo>/`. `issues.json` = newest-open-not-PR list with number, title, truncated body.

Minimum Russian fixture content (eligible user text):

- `sales-dashboard` `meta.json` description: дашборд продаж, воронка, выгрузка CSV. README: как выгрузить отчёт. One issue titled like «Экспорт дашборда продаж не завершается» (spinner / вечная загрузка).
- `crm` README: карточки клиентов, задания синхронизации. No issue that uniquely claims «синхронизация клиентов задерживается».
- `customer-portal` README: клиентский портал, профиль. Same: leave the vague sync report ambiguous between crm and portal.

**Functionality.** `FixtureKnowledgeSource.collect()` → `EvidencePack`. If any configured repo directory or file is missing → raise a typed error (compile will fail the run). Do not call GitHub.

**Verification.** `pytest tests/unit/test_fixture_source.py -q`

Cases: three repos load; pack contains the Russian export issue for sales-dashboard; delete `issues.json` → collect fails; configured repo with no directory → fail.

**Expected.** Pass with only disk files.

**Done.** Knowledge port is the only way triage sees tracker context. Production path will use `fixtures/github/` the same way.

---

### Step 8 — LLM port, prompts, schemas, fake, validators

**Purpose.** Isolate the model. Make invalid JSON and invented identities fail closed before Markdown exists.

**Create:**

- [src/tg_triage/ports/llm.py](../src/tg_triage/ports/llm.py)
- Prompts + JSON schemas as listed in [Implementation strategy, LLM integration](#llm-integration-initial-implementation--locked-here)
- [src/tg_triage/llm/validate.py](../src/tg_triage/llm/validate.py) — schema, coverage, identity checks
- [src/tg_triage/llm/http_client.py](../src/tg_triage/llm/http_client.py) — OpenAI-compatible `chat.completions`; `response_format` JSON if the provider accepts it; temperature 0
- [src/tg_triage/llm/debug_writer.py](../src/tg_triage/llm/debug_writer.py) — best-effort write; swallow IO errors
- [tests/fakes/fake_llm.py](../tests/fakes/fake_llm.py)
- [tests/unit/test_llm_validate.py](../tests/unit/test_llm_validate.py)
- [tests/unit/test_llm_http_client.py](../tests/unit/test_llm_http_client.py) — `httpx.MockTransport` or `respx`; **no live LLM**

**Functionality.** Map validated JSON → `TriageItem[]`. Fake can be scripted to return bad JSON once then good JSON (retry). HTTP client builds messages from prompt files + payload; does not know about GitHub tokens.

**Verification.** `pytest tests/unit/test_llm_validate.py tests/unit/test_llm_http_client.py -q`

Cases: valid cluster partition; missing problem id fails; invented `acme/secret` fails; `link_existing` with issue number absent from pack fails; `uncertain` + `unknown` repo passes; HTTP mock returns `{choices:[...]}` → parsed JSON; HTTP 500 → error (orchestrator will retry at a higher layer).

**Expected.** All offline. Prompt files exist and are read by the client (test can assert the system prompt contains “only identities present in the pack”).

**Done.** LLM package has no Telegram/GitHub adapters. Tests never hit OpenRouter/Ollama.

---

### Step 9 — Triage orchestrator (cluster then match)

**Purpose.** Finite two-stage workflow: load problems + pack → cluster → validate → match → validate → render → persist run.

**Create:** [src/tg_triage/application/compile.py](../src/tg_triage/application/compile.py), [tests/integration/test_compile.py](../tests/integration/test_compile.py), canned JSON under [tests/canned_llm/](../tests/canned_llm/).

**Functionality.**

- Empty ingested set → no LLM calls; return a “no problems” result
- Missing fixtures → `failed`, no LLM
- Cluster fail → retry once → still fail → `TriageRun.failed`
- Success → `generated_markdown` + `items_json` stored; status `pending`
- Write debug JSON best-effort
- `/compile` query: `created_at >= since_date` AND `lifecycle=ingested`

Canned scenario (must match later demo texts in [Two concrete real end-to-end demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios)): problems 101+102 cluster to the export item (`create_new`, `acme/sales-dashboard`); 104 → `link_existing` seeded issue; 103 → `uncertain`. Problem texts in the canned payload are Russian.

**Verification.** `pytest tests/integration/test_compile.py -q`

Assert: FakeLlm call count 2 (cluster, match); generated markdown parses to those three outcomes; coverage of {101,102,103,104}; debug writer invoked; empty period → 0 LLM calls; bad cluster JSON then good → pin the exact LLM call count in the test (retry + match); invented repo in canned match → failed run, no document payload.

**Expected.** Integration tests use temp SQLite + test fixtures + FakeLlm only.

**Done.** Orchestrator is deterministic aside from the port. Renderer, not the LLM, produced the `.md`.

---

### Step 10 — Application orchestrator (run state machine)

**Purpose.** Own allowlist-independent command use cases: supersede, store upload, execute preview/confirm/cancel. Still no Telegram library.

**Create:** [src/tg_triage/application/orchestrator.py](../src/tg_triage/application/orchestrator.py), [tests/integration/test_orchestrator.py](../tests/integration/test_orchestrator.py)

**Functionality.**

- `compile(owner_id, since_date)` — supersede previous `pending`/`awaiting_execute` with a warning flag; new run
- `store_upload(run_id, bytes)` — only `.md` bytes on that run → `awaiting_execute` (generated file becomes stale and must not be used)
- `execute_preview(run_id | latest)` — parse **uploaded_markdown**, not `generated_markdown`, not disk path
- `confirm` / `cancel`
- Preview without upload → error message, no GitHub

**Verification.** `pytest tests/integration/test_orchestrator.py -q`

Sequence: compile with canned LLM → store **edited** markdown that changes Priority and moves uncertain to Create → preview uses edited file (assert title/body from upload, not generated) → confirm → fake creates. Second compile while first is `awaiting_execute` → first status `superseded`; warning returned. Execute of superseded run rejected. Execute reading generated markdown instead of upload would fail the assertion — write that negative test.

**Expected.** Full HITL loop without Telegram.

**Done.** Uploaded Markdown is the only execute authority. Confirm still required.

---

### Step 11 — Fake end-to-end vertical slice (CI)

**Purpose.** One test that is the architecture proof, runnable without Telegram/GitHub/LLM/DB server.

**Create:** [tests/e2e/test_fake_workflow.py](../tests/e2e/test_fake_workflow.py)

**Functionality.** None new; wires [Step 4](#step-4--problem-intake-use-case) through [Step 10](#step-10--application-orchestrator-run-state-machine).

**Verification.** `pytest tests/e2e/test_fake_workflow.py -q`

Scripted events:

1. Four `ingest_group_text` calls with Russian texts from [Scenario A](#scenario-a--cluster--create--uncertain-human-resolves-one) plus one skip-match report
2. `compile(since=that day)`
3. Assert markdown has `## Create`, `## Uncertain`, and `## Skip` (canned match)
4. Mutate markdown string in memory (owner edit): set uncertain Repo to `acme/crm`, Body, move block to `## Create`
5. `store_upload` → `confirm`
6. Assert FakeIssueTracker created two issues (export + crm); skip recorded; problems on create/skip `linked`; excluded/uncertain-if-left stay `ingested` as designed

**Expected.** Green on a laptop with no tokens.

**Done.** This test is the regression gate for later adapter work. Do not weaken it to call live APIs.

---

### Step 12 — GitHub adapters (mocked HTTP) + seed / refresh / drop scripts

**Purpose.** Thin GitHub clients **outside** the domain. Three operator entry points over **fake** demo repos only:

- **seed** — create the configured made-up repos and populate them from versioned seed files (create-once)
- **refresh** — read those repos → write `fixtures/github/...`
- **drop** — delete those same seeded repos after an explicit confirmation that lists every `owner/repo`

**Create:**

- [src/tg_triage/infrastructure/github/client.py](../src/tg_triage/infrastructure/github/client.py) — REST: create repo, set description, put README, create issues, list open issues, get repo, delete repo
- [src/tg_triage/infrastructure/github/issue_tracker.py](../src/tg_triage/infrastructure/github/issue_tracker.py) — implements `IssueTracker`
- [src/tg_triage/operator/seed.py](../src/tg_triage/operator/seed.py)
- [src/tg_triage/operator/refresh.py](../src/tg_triage/operator/refresh.py)
- [src/tg_triage/operator/drop.py](../src/tg_triage/operator/drop.py)
- Seed tree (invented content only): [seed/github/acme/sales-dashboard/](../seed/github/acme/) (and `crm`, `customer-portal`) — `meta.json`, `README.md`, issues file. Prose in Russian (see [Step 7](#step-7--fixture-knowledge-source)). Do not copy README/issues from any real GitHub project.
- CLI entry points in `pyproject.toml`: `tg-triage-seed`, `tg-triage-refresh`, `tg-triage-drop`
- [README.md](../README.md) — confirm the workflow section names those three commands (written in [Step 1](#step-1--project-skeleton-and-configuration); adjust if the entry-point names differ)
- [tests/unit/test_github_adapters.py](../tests/unit/test_github_adapters.py)

**Functionality.**

- Seed must **not** write fixture files. Refresh must **not** create or delete issues/repos. Drop must **not** seed or refresh.
- Create adapter maps GitHub `{number, html_url}` → `IssueRef` + URL. Shared client; three scripts.
- **Create-once seed:** if `owner/repo` already exists, **skip it**, print a clear message (`Skipping acme/sales-dashboard: repository already exists. Run tg-triage-drop, then seed, to recreate.`). Do not update, retitle, or add duplicate seed issues.
- **Drop confirmation:** print the exact list of `owner/repo` that will be deleted (the configured demo list only — never an arbitrary GitHub repo). Wait for interactive confirmation that includes that list (operator must type `yes` after seeing the names, or type the repo names back). No default `--yes` that skips showing the list. Abort with no API deletes if confirmation does not match.
- Seed content (demo-critical, Russian): as in [Step 7](#step-7--fixture-knowledge-source). Vague «синхронизация клиентов» must remain ambiguous between `crm` and `customer-portal`.

**Verification.** `pytest tests/unit/test_github_adapters.py -q` with mocked httpx.

Cases: create issue POST `/repos/{owner}/{repo}/issues` JSON `{title, body}` only (no `labels`); refresh writes truncated issues.json (K=10, body 1000 chars) to a temp dir; seed emits create-repo + create-issue from seed files; second seed against “already exists” mock → 0 create-issue POSTs and a skip message; refresh after mock list does not POST issues; drop without matching confirmation → 0 DELETE calls; drop after confirmation → DELETE only the listed fake repos.

**Expected.** No live GitHub. Domain still has no `api.github.com` strings (`grep` `src/tg_triage/domain` and `src/tg_triage/application`). Seed files are not copies of real repositories.

**Done.** Three operator GitHub capabilities remain three entry points. Checked-in **test** fixtures stay; production `fixtures/github/` may be populated in [Step 13](#step-13--live-github-smoke-operator). [README.md](../README.md) workflow/CLI names match the entry points.

---

### Step 13 — Live GitHub smoke (operator)

**Purpose.** Prove seed, refresh, and create against real GitHub using **new fake repos** under `GITHUB_OWNER`. Not CI. Requires secrets — see [Open questions](#8-open-questions).

**Modify:** none required if [Step 12](#step-12--github-adapters-mocked-http--seed--refresh--drop-scripts) is complete. Operator fills `.env`: `GITHUB_TOKEN`, `GITHUB_OWNER`. Live config uses `{GITHUB_OWNER}/sales-dashboard` (etc.), not `acme` and not any pre-existing project.

**Functionality.** Same scripts, live network. Drop is **not** required for this smoke (destructive). Optional: after smoke, operator may `tg-triage-drop` (with confirmation) to clean up.

**Verification** (manual; coding agent must not invent tokens). Skip unless `GITHUB_TOKEN` and `GITHUB_OWNER` are set. Prefer `tests/smoke/test_github_live.py` marked `@pytest.mark.smoke` skipped unless `RUN_GITHUB_SMOKE=1`.

```text
# .env must contain GITHUB_TOKEN (repo create/delete scope) and GITHUB_OWNER
tg-triage-seed
# second run must print skip messages, not duplicate issues
tg-triage-seed
tg-triage-refresh
# assert fixtures/github/<GITHUB_OWNER>/sales-dashboard/issues.json contains the Russian export issue
RUN_GITHUB_SMOKE=1 pytest tests/smoke/test_github_live.py -q
```

The smoke test creates one throwaway issue via `IssueTracker.create` on a **seeded fake** repo and asserts a real `html_url` (GET 200). It must not target repositories outside the configured demo list.

**Expected.** Fake repos exist under `GITHUB_OWNER`; fixtures on disk; one created issue URL printed; second seed is a no-op with a skip message.

**Done.** Disposable demo world exists on GitHub. Refresh output is regenerated before compile demos. Live smoke is opt-in, not part of default `pytest`.

---

### Step 14 — Telegram adapter (offline) + bot wiring

**Purpose.** Map updates to application events; send unparsed `.md`; store document bytes; Confirm/Cancel. Group Privacy is an operator setting, not code.

**Create:**

- Port: [src/tg_triage/ports/telegram.py](../src/tg_triage/ports/telegram.py) outbound: `send_document`, `send_text`, `ask_confirm`
- [src/tg_triage/infrastructure/telegram/adapter.py](../src/tg_triage/infrastructure/telegram/adapter.py)
- [src/tg_triage/infrastructure/telegram/handlers.py](../src/tg_triage/infrastructure/telegram/handlers.py)
- [src/tg_triage/__main__.py](../src/tg_triage/__main__.py) — `python -m tg_triage` long polling
- [README.md](../README.md) — add `python -m tg_triage` to local setup (needs `.env` Telegram keys; default `pytest` still needs no tokens)
- [tests/unit/test_telegram_adapter.py](../tests/unit/test_telegram_adapter.py)

**Functionality (intake rules)** — [MVP System Specification, Telegram interaction model](mvp_system_specification.md#7-telegram-interaction-model):

- Only configured `TELEGRAM_GROUP_CHAT_ID` text from users → intake
- Commands and documents never become Problems (group or DM)
- Group `/compile` `/execute` → reject (“use DM”)
- DM from non-owner → ignore or short refuse
- Owner DM `/compile YYYY-MM-DD` → compile use case → `send_document` filename `triage-run-<id>.md`, caption optional, **no parse_mode** (unparsed)
- Owner document upload (`.md`) in DM → `store_upload` on latest pending/awaiting run (or run id in caption if present)
- `/execute` or `/execute <run-id>` → preview stored upload → inline Confirm / Cancel buttons
- Confirm callback → `execute_confirmed` → reply created/skipped URLs
- Cancel → `cancelled`
- New compile → one-line supersede warning

Inject FakeLlm / FakeIssueTracker in tests by constructing the orchestrator; production `main` wires real adapters from env.

**Verification.** `pytest tests/unit/test_telegram_adapter.py -q`

Feed synthetic `Update`-like payloads (or call handler functions with a fake `TelegramGateway` that records sends):

1. Group text → Problem row
2. Group `/compile` → no Problem, reject text, 0 compiles
3. Group document → no Problem
4. Other chat_id text → no Problem
5. Owner `/compile 2026-08-13` → send_document called with `.md` bytes and filename `triage-run-*.md`
6. Upload bytes → run `awaiting_execute`
7. `/execute` → ask_confirm with item counts (create/skip/uncertain)
8. Confirm → FakeIssueTracker called
9. Non-owner DM `/compile` → 0 compiles

**Expected.** No Telegram network. `python-telegram-bot` may be imported; domain still is not.

**Done.** Option A is implemented. `/execute` reads stored upload only.

---

### Step 15 — Live Telegram smoke (operator)

**Purpose.** Prove polling, group intake, DM document round-trip. Requires Telegram secrets — see [Open questions](#8-open-questions). LLM and GitHub may still be fakes if env points at stubs, **or** use the real LLM if [Step 16](#step-16--live-llm-compile-smoke-operator) is already done. Minimum: intake + file round-trip.

**Verification** (opt-in `RUN_TELEGRAM_SMOKE=1` or the checklist below — not default pytest). Skip if `TELEGRAM_BOT_TOKEN` is unset.

1. Create bot + group (BotFather); disable Group Privacy / add bot as admin; set `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `TELEGRAM_OWNER_USER_IDS`)
2. `python -m tg_triage`
3. Post one group message `SMOKE кнопка экспорта ничего не делает`
4. Query SQLite: that text exists as `ingested` (`python -c` using `SQLITE_PATH`)
5. Owner DM `/compile <today>` — if LLM is fake/stub, canned path still sends a file; if live LLM, a real file arrives
6. Download, re-upload the same file, `/execute`, Cancel — no GitHub writes
7. Confirm path only when GitHub token is intended (can wait for [demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios))

**Expected.** Problem row; `.md` document received in DM; upload stored; Cancel leaves GitHub untouched.

**Done.** Operator prerequisites documented in a short [docs/operator_setup.md](operator_setup.md) (bot, group privacy, env keys, seed/refresh/drop). No second UI. That file is created in this step if it does not exist yet.

---

### Step 16 — Live LLM compile smoke (operator)

**Purpose.** Prove host Ollama `qwen3:8b`, then OpenRouter `gpt-oss-20b:free` if needed, returns valid cluster+match JSON against **fake** fixtures. Requires LLM config — see [Open questions](#8-open-questions).

**Create:** [tests/smoke/test_llm_live.py](../tests/smoke/test_llm_live.py) skipped unless `RUN_LLM_SMOKE=1`; uses real `HttpLlmJudgment` + checked-in/refreshed fixtures + 3–4 canned **Russian** Problem texts; **does not** send Telegram or create GitHub issues.

**Verification.**

```text
# Preferred (host Ollama):
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_MODEL=qwen3:8b
RUN_LLM_SMOKE=1 pytest tests/smoke/test_llm_live.py -q
```

If Ollama is unavailable, rerun with OpenRouter: `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_MODEL=openai/gpt-oss-20b:free`, `LLM_API_KEY`.

Assert: valid partition; every repository is configured or `unknown`; do not hard-fail if the model clusters differently — assert **schema + identity validation passed** and markdown renders. Dump debug JSON under `debug/triage-runs/smoke/`.

**Expected.** One successful compile artifact. If JSON invalid, retry-once behavior is visible in debug files.

**Done.** Preferred model is acceptable for the demo, or Ollama fallback is confirmed on the host. No change to prompts to “make it always create” — uncertainty is success.

---

### Step 17 — Demo dry-run (fake, then real)

**Purpose.** Rehearse the two demo scenarios in [Two concrete real end-to-end demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios). First entirely on fakes (CI), then once on live Telegram+LLM+GitHub.

**Create:** [docs/demo_script.md](demo_script.md) — copy the tables from that section (exact Russian messages, since-date, owner edits).

**Verification.**

- Fake: extend or reuse `tests/e2e/test_fake_workflow.py` so both demo scenarios are encoded as tests
- Real: walk the tables in [Two concrete real end-to-end demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios); acceptance items in [Verification gates, Gate C](#gate-c--demo-day-acceptance)

**Expected.** Fake tests green. Real run produces GitHub issue URLs and one skip record.

**Done.** MVP success criteria in [MVP System Specification, MVP success criteria](mvp_system_specification.md#19-mvp-success-criteria) are each checked off.

---

## 6. Verification gates

**Purpose.** Three different people/roles prove three different things. This is not a second test suite and not a substitute for per-step verification. Use it only as a **gate**: do not call the MVP done until the matching gate is green.

| Gate | Who verifies | When | How | Expected if pass |
|---|---|---|---|---|
| A. Offline CI | Coding agent (default `pytest`, no extra env) | After [Steps 1–12](#step-1--project-skeleton-and-configuration) and [Step 14](#step-14--telegram-adapter-offline--bot-wiring); always before merging | `pytest -q` | Exit code 0. No live Telegram/GitHub/LLM. Proves the workflow with fakes. |
| B. Live smoke | Operator who owns the tokens | [Step 13](#step-13--live-github-smoke-operator) GitHub; [Step 15](#step-15--live-telegram-smoke-operator) Telegram; [Step 16](#step-16--live-llm-compile-smoke-operator) LLM | Opt-in env flags + scripts in those steps | Each smoke’s **Expected** line in that step. Failures stay in that external system, not in Gate A. |
| C. Demo-day | Presenter + stakeholder | [Step 17](#step-17--demo-dry-run-fake-then-real) using [demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios) | Walk Scenario A then B live | [MVP System Specification, MVP success criteria](mvp_system_specification.md#19-mvp-success-criteria): human-approved Markdown, creates + skip, at least one Uncertain, LLM did not create issues itself. |

**Gate A — what default pytest must prove** (coding agent checks this list only as “all corresponding tests exist and pass”, not as extra manual work):

- Group-like text persists as `Problem`; commands/documents do not (adapter tests)
- Duplicate `(chat_id, message_id)` does not duplicate rows
- Compile analogue loads only `ingested` rows in period
- Missing fixtures fail compile with 0 LLM calls
- Canned LLM: cluster coverage; invented repo fails; uncertain allowed
- Golden markdown parse/render; unknown `##` fails whole plan
- Preview does not create issues; Confirm creates; Skip does not write; Uncertain leaves `ingested`
- Retry execute does not duplicate creates
- New compile supersedes pending/awaiting; execute uses upload not generated file
- Seed create-once skips existing fake repos; drop without confirmation deletes nothing
- `grep` domain+application: no GitHub REST paths, no Telegram `Update`

**Gate B — live, opt-in only** (operator; skipped in CI):

- `tg-triage-seed` + `tg-triage-refresh` produce fixture files for the **configured fake** repos under `GITHUB_OWNER`
- Second `tg-triage-seed` prints skip messages and does not duplicate issues
- GitHub create smoke returns a real `html_url`
- Telegram: one group message → SQLite row; owner receives `triage-run-<id>.md`; upload + `/execute` + Cancel is safe
- Live LLM compile produces a schema-valid contract against fixtures (Ollama first, OpenRouter if the host model is down)

**Gate C — demo acceptance** (maps 1:1 to [MVP System Specification, MVP success criteria](mvp_system_specification.md#19-mvp-success-criteria)):

- Several free-form group reports (Russian)
- Problems in SQLite
- Owner `/compile` in DM
- Output shows clustered reports, a skip-able existing issue, a likely repo, a priority, at least one **Uncertain**
- Owner edits the file (resolve or leave uncertain; create vs skip)
- Upload + `/execute` + Confirm
- Issues created; skips recorded with refs/URLs
- LLM did not create issues by itself

---

## 7. Two concrete real end-to-end demo scenarios

**Shared setup (before either scenario)**

| Step | What to do | Who | Expected |
|---|---|---|---|
| 0.1 | Fill `.env`: `GITHUB_TOKEN`, `GITHUB_OWNER`, Telegram keys, LLM keys as in [Open questions](#8-open-questions) | Operator | Process can authenticate; no secrets in git |
| 0.2 | `tg-triage-seed` then `tg-triage-refresh` | Operator | Three **new fake** repos exist under `GITHUB_OWNER`; `fixtures/github/<owner>/sales-dashboard/issues.json` contains the Russian export issue (number `#N`) |
| 0.3 | `python -m tg_triage` | Operator | Bot is polling |
| 0.4 | Use today’s date as `SINCE` (`YYYY-MM-DD`) | List Owner | `/compile` will include the messages posted in this session |

Repos in play (made-up names only): `{GITHUB_OWNER}/sales-dashboard`, `{GITHUB_OWNER}/crm`, `{GITHUB_OWNER}/customer-portal`.

### Scenario A — Cluster + create + uncertain (human resolves one)

Goal: two similar export reports become one Create; a vague sync report is Uncertain; owner resolves Uncertain to Create on `crm`.

| Step | What to do | Who | Expected |
|---|---|---|---|
| A1 | In the reporter **group**, send: `Экспорт дашборда продаж больше не работает. Крутится загрузка.` | Reporter | New `Problem`, `lifecycle=ingested`, original text preserved |
| A2 | In the group, send: `На дашборде продаж экспорт зависает на спиннере. Нужен отчёт к планерке.` | Reporter | Second ingested Problem (different message id) |
| A3 | In the group, send: `Синхронизация клиентов задерживается.` | Reporter | Third ingested Problem |
| A4 | In a **DM** with the bot: `/compile SINCE` | List Owner | Bot sends unparsed `triage-run-<id>.md`. File has `## Legend`. Export reports share one `###` item under `## Create` (two Problem ids) **or** under `## Skip` if the model matched seed issue `#N` — if Skip, owner keeps the demo’s Create story by moving that block to `## Create` only when they judge it a new issue; prefer showing **clustering** (two ids, one item). Message A3 is under `## Uncertain` (`Repo: unknown`). Priority may be P1 on export. |
| A5 | Download the `.md`, edit outside Telegram: keep clustered export as Create on `sales-dashboard`; move the Uncertain `###` into `## Create`; set `Repo: {GITHUB_OWNER}/crm`; add a `Body`. Save. | List Owner | Edited file is the authority; generated file is stale |
| A6 | Upload the edited `.md` in the same DM | List Owner | Run status `awaiting_execute`; bot stored bytes |
| A7 | `/execute` then tap **Confirm** (not Cancel) | List Owner | Two GitHub issues created (export + crm). Bot replies with two URLs. Seeded export issue is **not** commented on. SQLite: three Problems `linked`. |

### Scenario B — Skip existing + leave uncertain

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

---

## 8. Open questions

No product/architecture forks remain ([MVP Architecture, Remaining architectural questions](mvp_architecture.md#13-remaining-architectural-questions)). What follows is **operator configuration**: when it is required, where it is set, and how live smoke runs. Steps 1–12 and 14 stay fully testable without these values.

### GitHub owner and token

**When required:** [Step 13](#step-13--live-github-smoke-operator) (live seed/refresh/create smoke), [Step 17](#step-17--demo-dry-run-fake-then-real) / [demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios). Optional for [Step 15](#step-15--live-telegram-smoke-operator) Confirm path. **Not** required for default `pytest`.

**Where to configure:**

- `.env` (gitignored): `GITHUB_TOKEN`, `GITHUB_OWNER`
- Template: [.env.example](../.env.example)
- Repo list: [config/demo.yaml](../config/demo.yaml) — live run substitutes `GITHUB_OWNER` for the fictional `acme` prefix. Names stay `sales-dashboard`, `crm`, `customer-portal` (created empty, then seeded). Never point this list at an unrelated existing GitHub project.

**Token:** a GitHub personal access token (or GitHub App token) with permission to **create, write, and delete** repositories under `GITHUB_OWNER`, and to create issues on those repos. Put it only in `.env` as `GITHUB_TOKEN`. The HTTP client sends `Authorization: Bearer <token>`. Coding agents must not invent or commit a token.

**How live smoke runs:** operator exports/sets `.env`, then `RUN_GITHUB_SMOKE=1 pytest tests/smoke/test_github_live.py -q` and/or `tg-triage-seed` + `tg-triage-refresh` as in [Step 13](#step-13--live-github-smoke-operator). If `GITHUB_TOKEN` is missing, smoke tests **skip** (do not fail Gate A).

### Telegram bot token, group chat id, owner user ids

**When required:** [Step 15](#step-15--live-telegram-smoke-operator), [Step 17](#step-17--demo-dry-run-fake-then-real) / demo scenarios. **Not** required for default `pytest` or [Step 14](#step-14--telegram-adapter-offline--bot-wiring) unit tests.

**Where to configure:** `.env`

- `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `TELEGRAM_GROUP_CHAT_ID` — numeric id of the pre-created reporter group (the app does not create the group)
- `TELEGRAM_OWNER_USER_IDS` — comma-separated numeric Telegram user ids allowed to `/compile`, upload, `/execute`, Confirm

Document the BotFather + Group Privacy operator steps in [docs/operator_setup.md](operator_setup.md) ([Step 15](#step-15--live-telegram-smoke-operator)).

**How live smoke runs:** operator starts `python -m tg_triage` (library reads `TELEGRAM_BOT_TOKEN`). Follow the [Step 15](#step-15--live-telegram-smoke-operator) checklist. If the token is unset, do not start the bot; unit tests still pass.

### LLM: host Ollama first, then OpenRouter

**When required:** [Step 16](#step-16--live-llm-compile-smoke-operator), and the real compile in [demo scenarios](#7-two-concrete-real-end-to-end-demo-scenarios). **Not** required for FakeLlm tests.

**Where to configure:** `.env` — `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Defaults in config: Ollama base URL + `qwen3:8b`.

**Preferred path:** host Ollama `qwen3:8b` with `LLM_BASE_URL=http://localhost:11434/v1` (API key unused).

**Fallback:** OpenRouter `openai/gpt-oss-20b:free` with `LLM_API_KEY` = OpenRouter key. No in-process model router.

**How live smoke runs:** `RUN_LLM_SMOKE=1 pytest tests/smoke/test_llm_live.py -q` with the default Ollama env; on failure, switch to OpenRouter env and rerun. Missing local server → skip, not Gate A failure.

### Seed create-once and drop

**Decision (locked):** seed is **create-once**. If the fake `owner/repo` already exists, skip it and print a clear message (do not update issues). To rebuild: `tg-triage-drop` (confirmation that lists every repo) then `tg-triage-seed`. Implemented in [Step 12](#step-12--github-adapters-mocked-http--seed--refresh--drop-scripts); live skip behavior proven in [Step 13](#step-13--live-github-smoke-operator).
