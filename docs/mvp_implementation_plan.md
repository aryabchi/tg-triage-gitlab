# MVP implementation plan

Greenfield Python project. Spec and architecture are locked: [mvp_system_specification.md](mvp_system_specification.md), [mvp_architecture.md](mvp_architecture.md). Follow [`.cursor/rules/implementation.mdc`](../.cursor/rules/implementation.mdc): Python 3.13, `pyproject.toml`, dedicated venv, type hints, no unnecessary `Any`, ports for every external system, secrets only via env.

A greenfield Python 3.13 bot that ingests Telegram group problems, compiles a validated Markdown contract via a two-stage LLM workflow against GitHub fixtures, then creates or skips GitHub issues only after the owner uploads and confirms. Every step is independently testable with fakes; live Telegram/GitHub/LLM appear only as explicit smoke steps.

Do not implement GitLab, RAG, labels-on-create, comments on existing issues, webhooks, or an agent loop.

---

## 1. Implementation strategy

Build **inside-out**, one verifiable slice at a time. Domain and application services are sync and port-driven. Adapters (Telegram, GitHub, LLM HTTP, SQLite) sit at the edge and are fakeable.

**First vertical slice that proves the architecture (no live APIs):** fake Telegram event → persist `Problem` → canned LLM JSON → validated Markdown → parse edited upload → Confirm → fake create/skip → `Problem.lifecycle = linked`.

**Control principle, enforced by construction:** LLM recommends structured JSON → application validates → deterministic code renders Markdown, parses the owner file, and performs GitHub writes. The LLM never receives a GitHub token and never authors the contract file.

**Locked implementation choices** (architecture deferred these; this plan does not):

- Package: `tg_triage` under `src/`
- Telegram library: `python-telegram-bot` v21+ (long polling, `Application`)
- HTTP: `httpx` (GitHub + OpenAI-compatible LLM)
- LLM DTOs / config: `pydantic` v2 + `pydantic-settings`
- Tests: `pytest` (+ `pytest-asyncio` only for Telegram adapter tests)
- DB: stdlib `sqlite3`, one file (tests use a temp file or `:memory:`)
- App services stay **sync**; the Telegram adapter is the async shell

**LLM integration (initial implementation — locked here)**

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

Default runtime: OpenRouter `openai/gpt-oss-20b:free`. Offline fallback is config-only (`LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=qwen3:8b`). No model router.

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
- GitHub adapter: **unit** (mocked HTTP); **smoke** live create (explicit step)
- Seed/refresh scripts: **unit** (mocked HTTP); **smoke** live (explicit step)
- Telegram adapter: **unit** (no network; fake `TelegramGateway` / recorded bot methods); **smoke** live polling (explicit step)
- Full workflow: **end-to-end with fakes** in CI; **real E2E** only for the two demo scenarios

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
8. GitHub adapter + seed/refresh (mocked, then live smoke) — tracker writes and demo world
9. Telegram adapter (fakes, then live smoke) — last runtime I/O; Option A round-trip
10. Live LLM compile smoke + full demo rehearsal — only after the workflow is deterministic

Telegram last: polling and chat IDs do not change domain behavior. GitHub seed before live compile: fixtures must contain the skip-match issue. Live LLM after canned-JSON tests: otherwise schema/coverage bugs are blamed on the model.

---

## 3–4. Numbered implementation steps

(Verification and Definition of Done are included in each step.)

### Step 1 — Project skeleton and configuration

**Purpose.** Installable Python 3.13 package, venv, test runner, env-based secrets, closed-world repo list.

**Create/modify:**

- [pyproject.toml](../pyproject.toml) — `requires-python = ">=3.13,<3.14"`; deps: `python-telegram-bot`, `httpx`, `pydantic`, `pydantic-settings`; dev: `pytest`, `ruff`
- [src/tg_triage/__init__.py](../src/tg_triage/__init__.py), [src/tg_triage/config.py](../src/tg_triage/config.py)
- [config/demo.yaml](../config/demo.yaml) — `repositories: [owner/sales-dashboard, owner/crm, owner/customer-portal]`, `k: 10`, `readme_max_chars: 2000`, `issue_body_max_chars: 1000`
- [.env.example](../.env.example) — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `TELEGRAM_OWNER_USER_IDS`, `GITHUB_TOKEN`, `GITHUB_OWNER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `SQLITE_PATH`
- [.gitignore](../.gitignore) — already ignores `.env`, `*.sqlite*`, `debug/`; keep that
- [tests/test_config.py](../tests/test_config.py)

**Functionality.** Load config from env + yaml. Refuse to start if required keys missing when a given entrypoint is used. No bot, no DB schema yet.

**Verification.**

```text
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -c "import tg_triage; print(tg_triage.__file__)"
pytest tests/test_config.py -q
```

Use a temp env in the test (do not read the real `.env`). Assert default `LLM_MODEL` is `openai/gpt-oss-20b:free` and default `LLM_BASE_URL` is `https://openrouter.ai/api/v1`. Assert `config/demo.yaml` parses to three `RepositoryId` values.

**Expected.** Package imports; config tests pass; no network.

**Done.** `pip install -e ".[dev]"` works on 3.13; config loads from env+yaml; secrets are not hardcoded; `.env.example` lists every key.

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

Sample input: `"The sales dashboard export doesn't work anymore.\nIt just keeps loading."` plus ids. Assert `original_text` exact match; `lifecycle == ingested`; linked_issue is None; duplicate message id → one row.

**Expected.** Unit tests pass with fake repo (SQLite optional extra case may reuse Step 3).

**Done.** Intake is deterministic, idempotent, and has no Telegram import.

---

### Step 5 — Markdown contract (render + parse)

**Purpose.** One schema both ways. Section heading **is** the action. `## Legend` skipped. Frozen example from architecture §4 is the golden file.

**Create:**

- [src/tg_triage/markdown/renderer.py](../src/tg_triage/markdown/renderer.py)
- [src/tg_triage/markdown/parser.py](../src/tg_triage/markdown/parser.py)
- [src/tg_triage/markdown/validate_plan.py](../src/tg_triage/markdown/validate_plan.py)
- [tests/golden/contract_example.md](../tests/golden/contract_example.md) — copy the architecture example (run 7, three sections)
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

**Expected.** Golden parse matches architecture example; round-trip stable; invalid files fail closed.

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

Use golden markdown + FakeIssueTracker that records calls. Assert: preview call count 0; after confirm, one `create` with title `Sales dashboard export hangs` and body containing the two-user summary; skip item → 0 extra creates; problems 101/102/104 `linked`; 103 still `ingested`; second execute → still one create total; invalid markdown → no creates.

**Expected.** Fake tracker is the only “GitHub”; tests pass offline.

**Done.** Execution never talks to LLM. Confirm is mandatory in the API (`execute_confirmed` vs `preview`).

---

### Step 7 — Fixture knowledge source

**Purpose.** `/compile` reads files only. Missing fixtures fail the run.

**Create:**

- Port: [src/tg_triage/ports/knowledge.py](../src/tg_triage/ports/knowledge.py)
- [src/tg_triage/infrastructure/fixtures.py](../src/tg_triage/infrastructure/fixtures.py)
- Sample test fixtures: `tests/fixtures/github/acme/sales-dashboard/{meta.json, README.md, issues.json}` (and crm, customer-portal)
- [tests/unit/test_fixture_source.py](../tests/unit/test_fixture_source.py)

Layout matches architecture: `fixtures/github/<owner>/<repo>/`. `issues.json` = newest-open-not-PR list with number, title, truncated body.

**Functionality.** `FixtureKnowledgeSource.collect()` → `EvidencePack`. If any configured repo directory or file is missing → raise a typed error (compile will fail the run). Do not call GitHub.

**Verification.** `pytest tests/unit/test_fixture_source.py -q`

Cases: three repos load; pack contains a seeded export issue for sales-dashboard; delete `issues.json` → collect fails; configured repo with no directory → fail.

**Expected.** Pass with only disk files.

**Done.** Knowledge port is the only way triage sees tracker context. Production path will use `fixtures/github/` the same way.

---

### Step 8 — LLM port, prompts, schemas, fake, validators

**Purpose.** Isolate the model. Make invalid JSON and invented identities fail closed before Markdown exists.

**Create:**

- [src/tg_triage/ports/llm.py](../src/tg_triage/ports/llm.py)
- Prompts + JSON schemas as listed in §1
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

Canned scenario (must match later demo texts): problems 101+102 cluster to export item (`create_new`, `acme/sales-dashboard`); 104 → `link_existing` seeded issue; 103 → `uncertain`.

**Verification.** `pytest tests/integration/test_compile.py -q`

Assert: FakeLlm call count 2 (cluster, match); generated markdown parses to those three outcomes; coverage of {101,102,103,104}; debug writer invoked; empty period → 0 LLM calls; bad cluster JSON then good → 3 LLM calls (retry+match) or 4 if match also retries — pin the exact count in the test; invented repo in canned match → failed run, no document payload.

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

**Functionality.** None new; wires Steps 4–10.

**Verification.** `pytest tests/e2e/test_fake_workflow.py -q`

Scripted events:

1. Three `ingest_group_text` calls (two export variants + one vague “sync is delayed”)
2. `compile(since=that day)`
3. Assert markdown has `## Create`, `## Uncertain`, and either Skip or Create depending on canned match (include a fourth ingest matching seeded issue for Skip)
4. Mutate markdown string in memory (owner edit): set uncertain Repo to `acme/crm`, Body, move block to `## Create`
5. `store_upload` → `confirm`
6. Assert FakeIssueTracker created two issues (export + crm); skip recorded; all four problems `linked` except if one excluded

**Expected.** Green on a laptop with no tokens.

**Done.** This test is the regression gate for later adapter work. Do not weaken it to call live APIs.

---

### Step 12 — GitHub adapters (mocked HTTP) + seed/refresh scripts

**Purpose.** Thin GitHub clients **outside** the domain. Seed writes demo repos from versioned definitions; refresh writes `fixtures/github/...`; execute create uses Issues API (title+body only).

**Create:**

- [src/tg_triage/infrastructure/github/client.py](../src/tg_triage/infrastructure/github/client.py) — REST: create repo (if missing), set description, put README, create issues, list open issues, get repo
- [src/tg_triage/infrastructure/github/issue_tracker.py](../src/tg_triage/infrastructure/github/issue_tracker.py) — implements `IssueTracker`
- [src/tg_triage/operator/seed.py](../src/tg_triage/operator/seed.py)
- [src/tg_triage/operator/refresh.py](../src/tg_triage/operator/refresh.py)
- Seed tree: [seed/github/acme/sales-dashboard/](../seed/github/acme/) (and crm, customer-portal) — `meta.json`, `README.md`, `issues.yaml` or json (export-hangs issue; login timeout; nothing that makes “sync delayed” unambiguous)
- CLI entry points in `pyproject.toml`: `tg-triage-seed`, `tg-triage-refresh`
- [tests/unit/test_github_adapters.py](../tests/unit/test_github_adapters.py)

**Functionality.** Seed must **not** write fixture files. Refresh must **not** create issues. Create adapter maps GitHub `{number, html_url}` → `IssueRef` + URL. Shared client; two scripts.

Seed content (demo-critical):

- `sales-dashboard` README mentions export/CSV; one open issue titled like “Sales dashboard export never finishes”
- `crm` README: customer records, sync jobs
- `customer-portal` README: customer-facing portal, profile sync — so a vague “customer synchronization is delayed” is legitimately uncertain

**Verification.** `pytest tests/unit/test_github_adapters.py -q` with mocked httpx.

Cases: create issue POST `/repos/{owner}/{repo}/issues` JSON `{title, body}` only (no `labels`); refresh writes truncated issues.json (K=10, body 1000 chars) to a temp dir; seed emits create-repo + create-issue calls from seed files; refresh after mock list does not POST issues.

**Expected.** No live GitHub. Domain still has no `api.github.com` strings (`grep` `src/tg_triage/domain` and `src/tg_triage/application`).

**Done.** Three GitHub capabilities remain three entry points. Checked-in **test** fixtures stay; production `fixtures/github/` may be populated in Step 13.

---

### Step 13 — Live GitHub smoke (operator)

**Purpose.** Prove seed, refresh, and create against real GitHub. Not CI.

**Modify:** none required if Step 12 is complete. Operator uses `.env` (`GITHUB_TOKEN`, `GITHUB_OWNER`). Replace `acme` in config with the real owner.

**Functionality.** Same scripts, live network.

**Verification** (manual commands; coding agent must not invent tokens):

```text
tg-triage-seed
tg-triage-refresh
# assert fixtures/github/<owner>/sales-dashboard/issues.json contains the export issue
# then a one-off pytest or CLI:
# create one throwaway issue via IssueTracker.create on a demo repo
# assert returned URL opens (or GET that issue number 200)
```

Optional tiny CLI `tg-triage-create-test --repo owner/sales-dashboard` is acceptable if it only exists for smoke; delete or gate behind env `SMOKE=1`. Prefer a `tests/smoke/test_github_live.py` marked `@pytest.mark.smoke` skipped unless `RUN_GITHUB_SMOKE=1`.

**Expected.** Repos exist; fixtures on disk; one created issue URL printed. Re-running seed does not blindly duplicate if the script is written to skip existing-named issues — if duplication is possible, document “run seed once; after demo only refresh.”

**Done.** Demo world exists on GitHub. Refresh output is committed or regenerated before compile demos. Live smoke is opt-in, not part of default `pytest`.

---

### Step 14 — Telegram adapter (offline) + bot wiring

**Purpose.** Map updates to application events; send unparsed `.md`; store document bytes; Confirm/Cancel. Group Privacy is an operator setting, not code.

**Create:**

- Port: [src/tg_triage/ports/telegram.py](../src/tg_triage/ports/telegram.py) outbound: `send_document`, `send_text`, `ask_confirm`
- [src/tg_triage/infrastructure/telegram/adapter.py](../src/tg_triage/infrastructure/telegram/adapter.py)
- [src/tg_triage/infrastructure/telegram/handlers.py](../src/tg_triage/infrastructure/telegram/handlers.py)
- [src/tg_triage/__main__.py](../src/tg_triage/__main__.py) — `python -m tg_triage` long polling
- [tests/unit/test_telegram_adapter.py](../tests/unit/test_telegram_adapter.py)

**Functionality (intake rules):**

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

**Purpose.** Prove polling, group intake, DM document round-trip. LLM and GitHub may still be fakes if env `LLM_BASE_URL` points at a local stub, **or** use the real LLM if Step 16 is already done. Minimum: intake + file round-trip.

**Verification** (opt-in `RUN_TELEGRAM_SMOKE=1` or a short operator checklist — not default pytest):

1. Create bot + group (BotFather); disable Group Privacy / admin; set `.env`
2. `python -m tg_triage`
3. Post one group message `SMOKE export button does nothing`
4. Query SQLite: that text exists as `ingested` (`python -c` using `SQLITE_PATH`)
5. Owner DM `/compile <today>` — if LLM is fake/stub, canned path still sends a file; if live LLM, a real file arrives
6. Download, re-upload the same file, `/execute`, Cancel — no GitHub writes
7. Confirm path only when GitHub token is intended (can wait for demo)

**Expected.** Problem row; `.md` document received in DM; upload stored; Cancel leaves GitHub untouched.

**Done.** Operator prerequisites documented in a short [operator_setup.md](operator_setup.md) (bot, group privacy, env keys, seed/refresh). No second UI.

---

### Step 16 — Live LLM compile smoke (operator)

**Purpose.** Prove gpt-oss-20b (or Ollama fallback) returns valid cluster+match JSON against real fixtures.

**Create:** [tests/smoke/test_llm_live.py](../tests/smoke/test_llm_live.py) skipped unless `RUN_LLM_SMOKE=1`; uses real `HttpLlmJudgment` + checked-in/refreshed fixtures + 3–4 canned Problem texts; **does not** send Telegram or create GitHub issues.

**Verification.**

```text
RUN_LLM_SMOKE=1 pytest tests/smoke/test_llm_live.py -q
```

Assert: valid partition; every repository is configured or `unknown`; at least one item may be uncertain (do not hard-fail if the model clusters differently — assert **schema + identity validation passed** and markdown renders). Dump debug JSON under `debug/triage-runs/smoke/`. If OpenRouter fails, rerun with Ollama env.

**Expected.** One successful compile artifact. If JSON invalid, retry-once behavior is visible in debug files.

**Done.** Default model is acceptable for the demo, or fallback is documented. No change to prompts to “make it always create” — uncertainty is success.

---

### Step 17 — Demo dry-run (fake, then real)

**Purpose.** Rehearse the two demo scenarios below. First entirely on fakes (CI), then once on live Telegram+LLM+GitHub.

**Create:** [demo_script.md](demo_script.md) with exact messages, since-date, expected sections, and owner edits. Do not use [demo_repos.txt](demo_repos.txt) (stale GitLab URL).

**Verification.**

- Fake: extend or reuse `tests/e2e/test_fake_workflow.py` so both demo scenarios are encoded as tests
- Real: operator checklist in §6–7

**Expected.** Fake tests green. Real run produces GitHub issue URLs and one skip record.

**Done.** MVP success criteria in spec §19 are each checked off.

---

## 6. Final MVP verification checklist

Default `pytest` (no extra env) is green: unit, integration, fake e2e.

Offline / fake:

- Group-like text persists as `Problem`; commands/documents would not (adapter tests)
- Duplicate `(chat_id, message_id)` does not duplicate rows
- `/compile` analogue loads only `ingested` rows in period
- Missing fixtures fail compile with 0 LLM calls
- Canned LLM: cluster coverage; invented repo fails; uncertain allowed
- Golden markdown parse/render; unknown `##` fails whole plan
- Preview does not create issues; Confirm creates; Skip does not write; Uncertain leaves `ingested`
- Retry execute does not duplicate creates
- New compile supersedes pending/awaiting; execute uses upload not generated file
- `grep` domain+application: no GitHub REST paths, no Telegram `Update`

Opt-in live:

- `tg-triage-seed` + `tg-triage-refresh` produce fixture files matching configured repos
- GitHub create smoke returns a real `html_url`
- Telegram: one group message → SQLite row; owner receives `triage-run-<id>.md`; upload + `/execute` + Cancel is safe
- Live LLM compile produces a schema-valid contract against fixtures

Demo (spec §19):

- Several free-form group reports
- Problems in SQLite
- Owner `/compile` in DM
- Output shows: clustered reports, a skip-able existing issue, a likely repo, a priority, at least one **Uncertain**
- Owner edits file (resolve or leave uncertain; create vs skip)
- Upload + `/execute` + Confirm
- Issues created; skips recorded with refs/URLs
- LLM did not create issues by itself

---

## 7. Two concrete real end-to-end demo scenarios

Assume configured repos `{GITHUB_OWNER}/sales-dashboard`, `.../crm`, `.../customer-portal`, fixtures refreshed after seed (export issue exists on sales-dashboard, e.g. `#N`). Owner allowlisted. Bot polling. Use today’s date as `SINCE`.

### Scenario A — Cluster + create + uncertain (human resolves one)

**Setup.** Seed+refresh done. No extra live issues required beyond seed.

**Reporter group messages (free-form, minutes apart):**

1. `The sales dashboard export doesn't work anymore. It just keeps loading.`
2. `Export on the sales dashboard is stuck on a spinner. Need this for the weekly report.`
3. `Customer synchronization is delayed.`

**Owner DM:** `/compile SINCE`

**Expect in the `.md`:** one `## Create` (or Skip if the model matches the seeded export issue — if Skip, owner **moves** that `###` to `## Create` only if the seeded issue is judged different; for this scenario prefer showing clustering: two problem ids on one item). One `## Uncertain` for message 3 (`Repo: unknown`). Priority may be P1 on export. Legend present.

**Owner edit (outside Telegram):** keep clustered export as Create on `sales-dashboard`; move uncertain block to `## Create`, set `Repo: {GITHUB_OWNER}/crm`, add a Body. Save.

**Upload** the file in DM. `/execute` → Confirm.

**Expect:** two GitHub issues (export, crm sync); SQLite: three Problems `linked`; Uncertain did not remain unexecuted; bot replies with two URLs. Seeded export issue is **not** commented on.

### Scenario B — Skip existing + leave uncertain

**Setup.** Scenario A already created the export issue, **or** rely on the seeded “export never finishes” issue. **Refresh fixtures** so the snapshot contains the issue to match. Ingest **new** problems only (`linked` ones must not reappear).

**Reporter group:**

1. `Dashboard CSV export still hangs forever — same as before.`
2. `Something is wrong with customers but not sure if it's the portal or CRM.`

**Owner:** `/compile SINCE`

**Expect:** item 1 → `## Skip` with `Existing: owner/sales-dashboard#N`; item 2 → `## Uncertain`.

**Owner edit:** leave Uncertain in place (do not create). Keep Skip. Upload. `/execute` Confirm.

**Expect:** **zero** new GitHub creates (Fake or live tracker create count 0); Skip recorded with `#N`; problem 1 `linked` to that `IssueRef`; problem 2 still `ingested`; bot lists Skipped and does not list a new URL for item 2. A later `/compile` can pick up problem 2 again.

These two scenarios together show clustering, matching, uncertainty, Markdown authority, confirm gate, create, skip-without-mutate, and lifecycle.

---

## 8. Open questions

None that block coding. Architecture remaining questions are closed. Telegram library is locked here to `python-telegram-bot`.

Operator-supplied at smoke/demo time (not design forks):

- Real `GITHUB_OWNER` / org that the token can create repos in
- BotFather token, group chat id, owner user ids
- Whether the demo machine uses OpenRouter or Ollama fallback that day

If seed should **update** existing GitHub issues vs create-once: prefer create-once (skip seed if repo exists and already has the named seed issues) so Step 13 re-runs are safe. That is an implementation detail for the seed script, not a product change.

Do not revive [demo_repos.txt](demo_repos.txt) GitLab URL for this MVP.
