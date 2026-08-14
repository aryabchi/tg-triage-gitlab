# MVP Architecture: Telegram Problems to Tracker Issues

This architecture follows the **locked** [mvp_system_specification.md](mvp_system_specification.md). The MVP tracker is **GitHub**. GitLab is the next adapter, not a runtime subsystem. The domain is tracker-neutral (`repository`, `issue number`, create-or-skip) so that later swap does not rewrite intake, triage, Markdown authority, or HITL.

The user prompt’s GitLab wording is treated as the **future tracker**, not the MVP implementation.

---

## 1. Architectural overview

The system is a **single local process**: a Telegram bot that polls, persists, compiles, and executes. There is no web UI, no job queue, no agent runtime, and no live tracker reads during triage.

Four workflow stages, plus cross-cutting persistence:

```text
Group text → Problem (deterministic)
Owner /compile → Problems + fixtures → LLM judgments → validated Markdown
Owner edits .md outside Telegram → upload stored on TriageRun
Owner /execute → parse → validate all → Confirm → GitHub create or skip
```

Control principle, enforced by construction:

```text
LLM recommends / reasons
        ↓
application validates
        ↓
deterministic application logic executes side effects
```

The LLM never holds a GitHub token and never creates issues. The owner’s uploaded Markdown is the only authority for writes.

---

## 2. Architecture principles

1. **LLM recommends; code executes.** Semantic judgment is the only LLM job. Persistence, coverage checks, schema validation, tracker identity checks, parsing, confirm, and API writes are deterministic.
2. **One Markdown contract.** Recommendation, human edit, and execution share one schema. No second format and no translation step.
3. **Closed-world knowledge.** `/compile` reads checked-in fixtures only. Live GitHub is used for **creates** (and by an operator refresh script), not for triage retrieval.
4. **Tracker-neutral domain.** Application code speaks `RepositoryId` + `IssueRef`. GitHub URLs, REST paths, and tokens stay in a thin adapter (and the refresh script).
5. **Evidence pack, not RAG.** Triage consumes an `EvidencePack` (snippets with provenance). The MVP pack is the fixture snapshot. Future sources implement the same port.
6. **Test the workflow without Telegram or GitHub.** Ports for Telegram events, LLM completion, knowledge, persistence, and issue creation are fakeable.
7. **No unjustified infrastructure.** One process, one local store, polling, fixtures on disk. No vector DB, no multi-agent framework, no workflow engine.

---

## 3. Major components

Only components with a closed-spec responsibility:

| Component | Kind | Why it exists |
|---|---|---|
| Telegram Adapter | External / infrastructure | Convert Telegram updates into domain events; send documents and confirm prompts. Telegram must not leak into domain types. |
| Application Orchestrator | Deterministic | Own command routing, owner allowlist, `TriageRun` state, confirm/cancel. This is not the LLM. |
| Problem Intake | Deterministic | Persist eligible group text as `Problem`. No classification. |
| Persistence | Infrastructure | Durable Problems, runs, uploads, execution results. |
| Knowledge Source (fixtures) | Deterministic infrastructure | Load the GitHub snapshot as an `EvidencePack`. |
| Fixture Refresh Script | Operator / external | Pull configured repos into fixture files. **Not** in the bot runtime. |
| Triage Orchestrator | Deterministic workflow | Two-stage procedure: cluster, then match; validate; render Markdown. |
| LLM Judgment Port | LLM | Structured semantic judgments only. No tools. No side effects. |
| Markdown Contract | Deterministic | Render LLM-validated items to `.md`; parse owner upload back to a plan. |
| Human Decision | Human | Edit the `.md` outside Telegram. Not a software service. |
| Execution Service | Deterministic | Validate-all-then-write; create or record skip; persist URLs for idempotency. |
| Issue Tracker Adapter | External (GitHub now) | Create issues. The only runtime write path to the tracker. |

Not components (on purpose): autonomous agent, vector store, catalog service, `HumanDecision` service, web UI, GitLab client in MVP, search API, comment-on-existing-issue.

---

## 4. Component responsibilities / inputs / outputs

### Telegram Adapter

- **Responsibility:** Map Telegram into application events; send unparsed `.md` documents and short Confirm/Cancel prompts.
- **Inputs:** Telegram updates (group text, owner DM commands, document uploads, confirm/cancel).
- **Outputs:** `GroupTextReceived`, `OwnerCompileRequested`, `OwnerDocumentUploaded`, `OwnerExecuteRequested`, `OwnerConfirmed` / `OwnerCancelled`. Outbound: `sendDocument`, plan summary, result URLs.
- **Dependencies:** Telegram Bot API (polling).
- **Kind:** External / infrastructure.
- **Why:** Isolates Telegram IDs, document bytes, 4096-char limits, and MarkdownV2 vs GFM mismatch from the domain.

Intake rules live here + orchestrator: commands and documents never become Problems; group `/compile`/`/execute` rejected; owner commands only in DM against a config allowlist.

### Application Orchestrator

- **Responsibility:** Workflow state machine for a `TriageRun`; dispatch intake / compile / execute; one-line warning when a new `/compile` supersedes a pending run.
- **Inputs:** Adapter events.
- **Outputs:** Calls into intake, triage, parse, execute; run status transitions.
- **Dependencies:** Persistence, intake, triage, markdown, execution, config.
- **Kind:** Deterministic.
- **Why:** The spec’s “thin application orchestrator” — Telegram commands, run state, confirm. Not LLM.

### Problem Intake

- **Responsibility:** Create a `Problem` from eligible group text. Preserve original wording. Idempotent on `(chat_id, message_id)`.
- **Inputs:** text, telegram user id, message id, chat id, timestamp.
- **Outputs:** Persisted `Problem` (`lifecycle = ingested`).
- **Dependencies:** Persistence.
- **Kind:** Deterministic. No LLM.

### Persistence

- **Responsibility:** Single local store for Problems, TriageRuns (generated Markdown, uploaded bytes, structured items, status), ExecutionResults.
- **Inputs / outputs:** Repository interfaces used by application services.
- **Kind:** Infrastructure.
- **Why:** Accumulation, date-range compile, Option A upload, idempotent execute.

### Knowledge Source (FixtureEvidenceSource)

- **Responsibility:** Load configured-repo snapshot into an `EvidencePack`. Missing fixtures fail the compile.
- **Inputs:** Configured repository list (identity only).
- **Outputs:** `EvidencePack` (repo metadata, README excerpt, top-K open issues).
- **Dependencies:** Fixture files on disk.
- **Kind:** Deterministic infrastructure.

### Fixture Refresh Script

- **Responsibility:** Operator-run GitHub **read** → write fixtures. Outside the bot.
- **Why:** Reproducible demo; no read-API failure mid-presentation; keeps live reads out of `/compile`.

### Triage Orchestrator

- **Responsibility:** Finite two-stage workflow (cluster → match → validate → render). Enforces coverage and closed-world identities.
- **Inputs:** `Problem[]` for `since-date`; `EvidencePack`.
- **Outputs:** Validated `TriageItem[]` + generated Markdown bytes on the `TriageRun`.
- **Dependencies:** Persistence, Knowledge Source, LLM port, Markdown renderer.
- **Kind:** Deterministic orchestration of LLM calls.
- **Why:** The spec replaced an autonomous agent with this procedure to bound reliability risk.

### LLM Judgment Port

- **Responsibility:** Two structured completions. No tools. No GitHub/Telegram access.
- **Inputs:** (1) problem texts + ids; (2) clustered items + evidence pack.
- **Outputs:** JSON matching the cluster/match schemas.
- **Kind:** LLM.
- **Why:** Natural language, clustering, repo/issue inference, priority, uncertainty — the only place semantics belong.

### Markdown Contract (renderer + parser)

- **Responsibility:** One schema both ways. Renderer is deterministic from `TriageItem[]`. Parser is the only path from owner upload to execution plan.
- **Kind:** Deterministic.
- **Why:** Owner file is authoritative; Telegram chat text cannot carry GFM reliably.

### Execution Service + Issue Tracker Adapter

- **Responsibility:** Parse stored upload → validate entire plan → after Confirm, create issues or record skips. Persist created URLs. Retry skips already-created items.
- **Inputs:** Stored Markdown bytes; owner confirm.
- **Outputs:** `ExecutionResult` (created `owner/repo#n` + URL, or skipped existing ref); Problem lifecycle updates.
- **Dependencies:** Persistence, GitHub Issues API (create only).
- **Kind:** Deterministic + external write.
- **Why:** LLM must not create issues; validate-all-then-write; no comments on existing issues.

---

## 5. Domain model

Conservative. No `HumanDecision` aggregate. No free-floating `GitLabIssueLink` / `GitHubIssueLink`.

### Problem — persistent

One original Telegram report.

- `id` (internal)
- `original_text`
- `telegram_message_id`, `telegram_chat_id`, `telegram_user`
- `created_at`
- `lifecycle`: `ingested` | `linked` (after a successful execute that referenced it)
- optional `linked_issue`: `IssueRef` (filled on execute; not a separate entity)

Independent of runs. May appear in more than one historical `TriageRun` (a new `/compile` is an independent run). No metadata bag.

### TriageRun — persistent

One `/compile`.

- `id`
- `since_date`
- `owner_telegram_user_id`
- `status`: `pending` | `awaiting_execute` | `executed` | `cancelled` | `superseded` | `failed`
- `generated_markdown` (bytes/text)
- `uploaded_markdown` (bytes/text, Option A; **this is the decision**)
- `items_json` (validated `TriageItem[]` from compile — audit/debug, not the execute authority)
- `execution_result` (after execute)
- timestamps

Status meaning:

- `pending` — compile succeeded, document sent; upload may still be missing
- `awaiting_execute` — owner upload stored
- Confirm/Cancel is a short-lived field or the same run staying `awaiting_execute` until confirm (no extra product status)
- New `/compile` marks the previous pending/awaiting run `superseded`

### TriageItem — persistent only as JSON on the run, not its own aggregate

In-memory during compile; stored on `TriageRun` after validation for audit. **Execute does not read this JSON**; it reads the uploaded Markdown.

- `summary` / title
- `problem_ids[]` (coverage: every period Problem id in exactly one item)
- `outcome`: `create_new` | `link_existing` | `uncertain` | `exclude`
- `repository`: `RepositoryId` or `unknown`
- optional `existing_issue`: `IssueRef`
- optional `priority`
- `rationale` (short, user-facing; no chain-of-thought)
- `body` (for create)

Uncertain is a first-class **item outcome**, not a parallel collection.

### HumanDecision — not an entity

The uploaded Markdown on `TriageRun` **is** the decision. A parsed plan exists only in memory between `/execute` and Confirm. Persisting a second decision model would duplicate the contract and drift from the file the owner edited.

### GitLabIssueLink / GitHubIssueLink — not an entity

Links live on:

- recommended `TriageItem.existing_issue`
- executed `ExecutionResult` rows
- `Problem.linked_issue` after execute

### ExecutionResult — persistent (embedded on the run)

Per executed item: `create` → `IssueRef` + URL, or `skip` → existing `IssueRef`, plus `problem_ids`. Required so a later `/execute` does not duplicate creates.

### Value objects (not tables)

- `RepositoryId` — opaque path, MVP form `owner/repo`
- `IssueRef` — `RepositoryId` + number
- `EvidencePack` / `EvidenceSnippet` — see §8
- `MarkdownPlan` — parsed, validated, in-memory execution plan

---

## 6. Triage / LLM architecture

### Alternatives

**A. Deterministic workflow + LLM reasoning (recommended, and already locked by the spec)**

The application always:

1. Loads Problems for `since-date`.
2. Loads the fixture `EvidencePack`.
3. Calls LLM **cluster** (problems only).
4. Validates coverage (partition of problem ids). One retry on schema/coverage failure, then fail the run.
5. Calls LLM **match** (items + evidence pack).
6. Validates repos against config and issue numbers against the snapshot. Invented identities rejected. Uncertain allowed.
7. Renders Markdown. Persists run. Sends document.

The LLM does not choose retrieval, tools, or when to stop.

**B. Autonomous tool-using agent (rejected for MVP)**

The LLM would decide what to retrieve, which GitHub searches to run, and whether to continue. Spec explicitly gave this up for the two-week constraint.

| Criterion | A | B |
|---|---|---|
| Implementation complexity | Two prompts + validators | Tool loop, stop conditions, budget, tracing |
| Reliability | Finite, fail-closed | Open-ended; demo-fragile |
| Debuggability | Stage logs + JSON artifacts | Path-dependent traces |
| Reproducibility | Same problems + fixtures + temperature 0 ≈ same structure | Tool order varies |
| Observability | Two timed calls, token counts | N calls, hard to SLA |
| Meaningful agentic behavior | Clustering, matching, uncertainty, priority — the semantics that matter | Tool-use theater the demo does not need |
| Future extensibility | Swap fixture source for live GitLab retriever behind `EvidencePack` | Agent loop still needs the same validation/HITL walls |

**Recommendation: A.**

### Where LLM/agentic behavior still exists (under A)

Not “no AI.” The LLM still:

- normalizes/summarizes unstructured reports;
- clusters related Problems into one item;
- interprets README/issue text in the pack;
- proposes repository or `unknown`;
- proposes existing issue or create;
- recommends priority;
- marks `uncertain` when the snapshot cannot support a classification.

What it must **not** do: call GitHub, choose extra retrieval, skip coverage, emit Markdown it invented as already-authoritative, or create issues.

### LLM boundary (precise)

**Receives**

- Stage 1: problem id + original text (+ timestamp optional). No fixtures.
- Stage 2: validated clusters + full `EvidencePack` (repo id, short description, README excerpt, issue number/title/truncated body). Instruction: only use identities present in the pack/config; otherwise `uncertain`.

**Tools:** none. Retrieval is application-side. No GitHub token in the LLM client.

**Must return (structured JSON, not Markdown)**

Stage 1 — `{ items: [{ summary, problem_ids[] }] }` covering every id exactly once.

Stage 2 — per item: `outcome`, `repository` (`owner/repo` or `unknown`), optional `existing`, `priority`, `title`, `body`, `rationale`.

Application **renders** Markdown from that JSON. The LLM does not author the contract file directly (avoids heading drift). Optional: if the model is worse at JSON than at the contract shape, still validate into the same schema before persist; renderer remains canonical.

**Application validates**

- JSON schema
- coverage partition
- `repository` in config or `unknown`
- `existing` issue number exists in that repo’s snapshot (for compile recommendations)
- `link_existing` requires a valid `existing`; `uncertain` must not invent a repo
- one retry then `TriageRun.failed`

Execute-time validation is against **config + Markdown schema**, not against the snapshot (owner may reference an issue the snapshot does not contain; skip records the ref without writing to it). Invented **repo** names still rejected against config.

**Deterministic decisions**

Intake, allowlist, date filter, fixture load, coverage, identity checks, Markdown render/parse, confirm gate, GitHub create/skip, idempotency.

**When the LLM is uncertain**

It returns `outcome = uncertain`, `repository = unknown`. Rendered under `## Uncertain`. Not executed as create. Owner may resolve in the file or leave it. The system must not invent a repository to complete the file.

---

## 7. External integration boundaries

### Telegram → domain

Adapter converts; domain never sees `Update` objects.

| Telegram | Domain |
|---|---|
| Group user text | `Problem` fields |
| Commands / documents | Orchestrator events, never Problems |
| Owner DM `/compile <date>` | New `TriageRun` |
| Bot `sendDocument` | Generated contract (unparsed `.md`) |
| Owner uploaded `.md` | `TriageRun.uploaded_markdown` |
| `/execute` | Parse **stored upload**, not the original bot attachment |
| Confirm / Cancel | Gate on GitHub writes |

A local `triage-runs/` file copy may exist for debugging; `/execute` must not read a path on the bot host.

### Tracker (GitHub now, GitLab later)

Four capabilities, **not** one GitHub god-client inside the domain:

| Capability | MVP implementation | Domain sees |
|---|---|---|
| Knowledge for triage | Fixture files via `KnowledgeSource` | `EvidencePack` |
| Search existing issues | Not a live search; match against snapshot issues in the pack | `IssueRef` proposals + validation |
| Identify projects/repos | Configured closed list | `RepositoryId` |
| Create issues | Live GitHub Issues API after Confirm | `IssueTracker.create(...)` → `IssueRef` + URL |

Runtime compile **does not** call GitHub. Refresh script is the only read client. Execution adapter is the only write client. Do not scatter GitHub URLs/REST through orchestrator or triage.

Later GitLab: new refresh script + new `IssueTracker` adapter (project path + IID). Evidence snippet *shape* stays. No multi-tracker plugin framework in the MVP — only a small port.

---

## 8. Knowledge retrieval architecture

MVP knowledge = persisted Problems + fixture snapshot (repo metadata, README excerpt, top-K open issues). No Wiki/Confluence/RAG in runtime.

**Smallest extension point** (not a vector store):

```text
KnowledgeSource.collect(context) -> EvidencePack

EvidenceSnippet
  source_id     (e.g. "github-fixtures")
  kind          (repo_meta | readme | issue | later: wiki)
  locator       (RepositoryId, optional issue number)
  title
  text
  provenance    (file path / URL for humans, not for RAG)
```

`TriageContext` for MVP can be empty or just the configured repo list. `FixtureKnowledgeSource.collect` returns the **whole** snapshot. Closed world; no query planner.

Later: a `ConfluenceSource` (or GitLab wiki) implements the same port. The triage orchestrator **concatenates** packs (or takes a configured list of sources). Matching/validation still use tracker identities from the tracker pack; extra sources are additional text with provenance. No change to `TriageItem`, Markdown contract, or HITL.

Do **not** add embeddings, chunking pipelines, or a broker until a real second corpus exists.

---

## 9. Persistence architecture

**Need durable state**

- Every `Problem`
- Every `TriageRun` including generated Markdown, owner upload bytes, status, validated items JSON, execution results
- Problem ↔ issue link after execute (idempotency + lifecycle)

**Do not persist**

- Raw Telegram updates
- LLM chain-of-thought / raw prompts (optional debug log file is enough; not a domain table)
- `HumanDecision` rows
- Embeddings
- Live GitHub catalog
- Fixture snapshot in the DB (files in the repo)

**TriageRun vs Problems:** Problems are the accumulation store. A run *selects* by `created_at >= since_date` at compile time and stores the id set inside items. Execute updates those Problems’ lifecycle. Runs do not own Problems.

**Recommendation: SQLite (one file), not a client-server DB, not a bag of JSON files as the system of record.**

Why SQLite:

- Date-range query and unique `(chat_id, message_id)` are real
- Status transitions and “persist created URL before next item” want a transaction
- Upload bytes belong next to the run
- Still one local file, no extra process — matches “local demo process”

Why not JSON/files as SoR: easy to start, weak on uniqueness, concurrent polling vs command handling, and partial-create recovery. A debug dump of Markdown beside SQLite is fine.

Why not Postgres: unjustified hosting for a two-week local demo.

Config (tokens, owner ids, repo list, `K`, truncation) stays in env + a small config file, not the DB.

---

## 10. End-to-end workflows

Distinguish three layers everywhere:

- **Orchestration:** Telegram command/run state (Application Orchestrator)
- **Domain logic:** coverage, contract schema, outcomes, idempotency
- **Infrastructure:** Telegram API, SQLite, fixture files, LLM HTTP, GitHub create

### A. Telegram problem ingestion

1. Adapter: group text, not command/document.
2. Intake: map to `Problem`, persist (`ingested`).
3. No LLM. No clustering.

**State:** new `Problem` row.

### B. List Owner requests a triage run

1. DM `/compile <since-date>` from allowlisted owner.
2. Orchestrator supersedes any pending/awaiting run (warning).
3. Creates `TriageRun` (`pending` once compile succeeds).

### C. Load Problems for the period

1. Persistence: `created_at >= since-date` (and eligible).
2. Empty set → message owner, no LLM.
3. Load `EvidencePack`; missing fixtures → fail run.

**State:** in-memory problem list + pack; not a new entity.

### D. Triage

1. Cluster LLM → validate coverage.
2. Match LLM + pack → validate identities.
3. Render Markdown; store `generated_markdown` + `items_json`.
4. `sendDocument` `triage-run-<id>.md`.

**State:** `TriageRun` pending with generated artifact.

### E. Human review/editing

1. Owner edits outside Telegram (any device).
2. Uploads `.md` in the same DM.
3. Orchestrator stores bytes on the run → `awaiting_execute`.
4. No LLM. No UI.

**State:** `uploaded_markdown` is authoritative. Generated file is stale.

### F. Parsing the final Markdown

1. `/execute` or `/execute <run-id>` reads **stored upload** (not bot original, not host path).
2. Parse → `MarkdownPlan`.
3. Validate whole plan (schema, actions, repos in config, skip has `existing`, uncertain not create).
4. Show short plan; Confirm/Cancel. No GitHub yet.

**State:** still `awaiting_execute` until confirm; cancel → `cancelled`.

### G. Creating tracker issues

1. For each `create`: `IssueTracker.create`; persist URL immediately.
2. For each `skip`: record `IssueRef`; **no** write to the existing issue.
3. Uncertain / exclude: no write.
4. Update Problem lifecycle; run → `executed`; reply with URLs.

**State:** `ExecutionResult` on the run; Problems `linked`. Re-execute skips items that already have a created URL.

---

## 11. Mermaid diagrams

### 11.1 System context

```mermaid
flowchart LR
    reporter[Reporter]
    owner[ListOwner]
    operator[Operator]
    telegram[Telegram]
    app[TriageBot]
    llm[LLMProvider]
    github[GitHub]
    store[(SQLite)]
    fixtures[FixtureFiles]

    reporter -->|group text| telegram
    owner -->|DM compile upload execute confirm| telegram
    telegram --> app
    app --> telegram
    app --> store
    app -->|structured judgments| llm
    app -->|create issue only| github
    app --> fixtures
    operator -->|refresh script| github
    github -->|snapshot| fixtures
```

### 11.2 Container / component

```mermaid
flowchart TB
    subgraph process [SingleBotProcess]
        adapter[TelegramAdapter]
        orch[ApplicationOrchestrator]
        intake[ProblemIntake]
        triage[TriageOrchestrator]
        llmport[LLMJudgmentPort]
        md[MarkdownRendererParser]
        exec[ExecutionService]
        know[FixtureKnowledgeSource]
        repos[Repositories]
    end

    telegram[TelegramAPI]
    sqlite[(SQLite)]
    files[FixtureFiles]
    llm[LLMAPI]
    gh[GitHubIssuesAPI]
    human[OwnerExternalEditor]

    telegram --> adapter
    adapter --> orch
    orch --> intake
    orch --> triage
    orch --> md
    orch --> exec
    intake --> repos
    triage --> know
    triage --> llmport
    triage --> md
    know --> files
    llmport --> llm
    exec --> md
    exec --> gh
    repos --> sqlite
    orch --> repos
    adapter -->|sendDocument| telegram
    telegram -->|upload md| human
    human -->|edited md| telegram
```

### 11.3 End-to-end sequence

```mermaid
sequenceDiagram
    participant R as Reporter
    participant TG as Telegram
    participant O as Orchestrator
    participant P as ProblemStore
    participant T as TriageOrchestrator
    participant L as LLM
    participant F as Fixtures
    participant Owner as ListOwner
    participant E as ExecutionService
    participant GH as GitHub

    R->>TG: group text
    TG->>O: GroupTextReceived
    O->>P: persist Problem

    Owner->>TG: /compile since-date
    TG->>O: OwnerCompileRequested
    O->>P: load Problems in period
    O->>F: load EvidencePack
    O->>T: compile
    T->>L: cluster JSON
    L-->>T: clusters
    T->>T: validate coverage
    T->>L: match JSON plus pack
    L-->>T: items
    T->>T: validate identities
    T-->>O: Markdown plus items
    O->>P: save TriageRun
    O->>TG: sendDocument md
    TG->>Owner: triage-run-id.md

    Owner->>Owner: edit outside Telegram
    Owner->>TG: upload edited md
    TG->>O: store upload on run

    Owner->>TG: /execute
    TG->>O: parse stored upload
    O->>Owner: plan Confirm or Cancel
    Owner->>TG: Confirm
    TG->>E: execute
    E->>GH: create issue
    GH-->>E: number plus URL
    E->>P: ExecutionResult and Problem linked
    E->>TG: created and skipped URLs
```

Human Decision is the owner’s edit between `sendDocument` and upload — no software box.

---

## 12. Key architectural decisions

**Decision:** Deterministic two-stage triage (cluster, then match) with LLM structured judgments; not an autonomous tool-using agent.  
**Reason:** Spec-locked; finite, fail-closed, demo-reliable; still shows clustering, matching, uncertainty.  
**Alternative:** Tool-using agent over live GitHub.  
**Why rejected:** Largest two-week reliability risk; live reads already replaced by fixtures; HITL still required so autonomy cannot complete the workflow.

**Decision:** SQLite as the single local system of record.  
**Reason:** Date queries, uniqueness, transactions for partial create, blobs for uploads; one file.  
**Alternative:** JSON files or Postgres.  
**Why rejected:** JSON is weak for concurrent updates and idempotency; Postgres is extra ops for a local demo.

**Decision:** Tracker port with GitHub create adapter; knowledge via fixtures, not a GitHub read client in the bot.  
**Reason:** Spec: compile does not call GitHub for reads; domain must stay GitLab-ready.  
**Alternative:** Live GitHub search during compile; GitLab in MVP.  
**Why rejected:** Live reads and GitLab were explicitly deferred; dual-tracker framework is out of scope.

**Decision:** LLM has zero tools and zero tracker credentials; application supplies the evidence pack and validates identities.  
**Reason:** Enforce recommend → validate → execute.  
**Alternative:** Give the model GitHub tools.  
**Why rejected:** Would let the LLM approach side effects and invented issues; contradicts the control principle.

**Decision:** `KnowledgeSource.collect → EvidencePack` as the only knowledge extension point.  
**Reason:** Smallest seam for Wiki/Confluence later without RAG.  
**Alternative:** Vector store / RAG now.  
**Why rejected:** Unjustified by demo size; would consume the two-week budget.

**Decision:** Uploaded Markdown on `TriageRun` is the human decision; no `HumanDecision` entity; execute parses that file only.  
**Reason:** Owner file is authoritative; Option A survives phone/laptop switch.  
**Alternative:** Web UI, second agent, or execute-from-host-path.  
**Why rejected:** Out of scope; path execute fails across devices; chat-paste hits Telegram limits.

**Decision:** Single-process orchestrator driven by Telegram polling; no workflow engine.  
**Reason:** Four sequential stages, one owner, tens of problems.  
**Alternative:** Temporal/Celery/webhooks/multi-service.  
**Why rejected:** Not justified by MVP; more failure modes for a demo.

**Decision:** Domain names stay tracker-neutral (`RepositoryId`, `IssueRef`, `IssueTracker`).  
**Reason:** GitHub-specific types in the domain would block GitLab.  
**Alternative:** `GitHubIssue` throughout.  
**Why rejected:** Spec: that would block the transition.

**Decision:** Priority is a recommended field in the contract; map to a pre-created GitHub label if it exists on demo repos, otherwise Markdown grouping only.  
**Reason:** GitHub has no native priority.  
**Alternative:** Fake a native priority field.  
**Why rejected:** Would not survive GitHub or many GitLab instances.

---

## 13. Remaining architectural questions

Product/workflow questions in the spec are closed. Left as implementation choices, not blockers:

1. **LLM vendor** — any structured-output HTTP API behind the judgment port (OpenAI-compatible is enough). Not an architecture fork.
2. **Exact Markdown heading/list syntax** — freeze in implementation to match the spec’s illustrative shape (`## Create` / `## Skip` / `## Uncertain`, list fields, `Body: |`). Parser tests pin it.
3. **Fixture layout and K / truncation** — e.g. `fixtures/github/<owner>/<repo>/{meta.json,README.md,issues.json}`; K and body length in config.
4. **Priority write mapping** — try configured label names; if missing, omit labels (title/body still carry the grouping).
5. **Telegram library and polling details** — long polling, single process; webhook unnecessary for local demo.
6. **Whether to log raw LLM JSON to a debug directory** — useful, not required for correctness.

Do not reopen: agent loop, live compile reads, RAG, UI, GitLab runtime, comments on existing issues.

---

## 14. High-level implementation structure

Layers follow the architecture (not a package dump):

**Domain** — `Problem`, `TriageRun`, `TriageItem`, `IssueRef`, `EvidencePack`, coverage/outcome invariants. No Telegram/GitHub types.

**Application** — intake use case; compile use case (load → cluster → match → validate → render → persist → send); execute use case (parse upload → validate all → confirm → write); run supersede/cancel. Orchestration lives here.

**Agent / LLM** — prompt templates + JSON schemas for cluster and match; mapper from LLM JSON to `TriageItem`. No adapters to Telegram/GitHub.

**Interfaces / ports** — `ProblemRepository`, `TriageRunRepository`, `KnowledgeSource`, `LlmJudgment`, `IssueTracker`, `TelegramGateway` (outbound).

**Infrastructure / adapters** — Telegram polling + inbound mapping; SQLite repositories; fixture file loader; GitHub create client; LLM HTTP client; Markdown renderer/parser; config; operator refresh script (GitHub read → files).

Tests sit on application + domain with fakes for every port. Fixture + golden Markdown files make compile/execute demo-reproducible without live Telegram/GitHub/LLM.

---

## 15. Recommended implementation sequence

Order by risk and demo path; keep each slice end-to-end testable with fakes:

1. **Domain + SQLite + intake** — persist Problems; idempotent message ids; no Telegram yet (adapter fake).
2. **Markdown contract** — renderer and parser with golden files; validation of create/skip/uncertain; no LLM.
3. **Execution against a fake IssueTracker** — confirm gate, persist URLs, idempotent retry; then swap in GitHub create.
4. **Fixtures + EvidencePack** — refresh script against demo repos; compile fails if files missing.
5. **Triage orchestrator + LLM port** — cluster then match, coverage and identity validation, one retry; canned LLM responses in tests; live LLM last.
6. **Telegram Option A** — polling, group intake, owner DM, sendDocument, store upload, `/execute`, Confirm/Cancel.
7. **Demo seed** — two similar export reports (cluster), one vague (uncertain), one matching a seeded issue (skip); refresh fixtures; dry-run the full script.

Do not start with package taxonomy, agent frameworks, or GitLab clients. The first vertical slice that proves the architecture is: **fake Telegram event → Problem → fake LLM JSON → Markdown → parse edited file → fake create/skip.**
