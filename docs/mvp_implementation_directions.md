# MVP implementation directions (pre-demo)

Numbered plan Steps 1–17 are done. This file is **not** a new implementation plan. It is what to do before presenting: latency, extra scenarios, ops, and storytelling. Do not reopen [out-of-scope items](mvp_system_specification.md#4-out-of-scope-for-this-mvp) (RAG, live GitHub during compile, comments on skips, GitLab, Mini App, agent loop).

Live A+B on host `qwen3:8b` already proved Gate C mechanically (`debug/7`, `debug/9`). The remaining risk is **wall-clock and a dirty demo world**, not missing product slices.

## Priority

| Priority | Meaning |
|---|---|
| **P0** | Without this, the live walk is likely to fail, stall past a short slot, or show the wrong GitHub story |
| **P1** | Strongly recommended in the dress rehearsal; cheap relative to embarrassment |
| **P2** | Extra talking-point scenarios if time remains |
| **P3** | After the demo, or keep out unless product reopens it |

## Index

| ID | Priority | Item |
|---|---|---|
| D1 | P0 | Pick a **faster** OpenAI-compatible model (keep qwen as known-good fallback) |
| D2 | P0 | **Reset** GitHub demo repos + SQLite before the real walk |
| D3 | P0 | Timed **dress rehearsal** of A then B with the chosen model |
| D4 | P1 | Immediate “compile started” DM so `/compile` is not silent for minutes |
| D5 | P1 | Shrink the **evidence pack** if the model is still slow |
| D6 | P1 | Show an **owner Markdown edit** (Uncertain → Create, or Body tweak) |
| D7 | P1 | Rehearse **Cancel** (LLM never writes GitHub) |
| D8 | P1 | Pre-warm Ollama, restart the bot, script the wait |
| D9 | P1 | Phone ↔ laptop file path; resend upload on Telegram timeout |
| D10 | P2 | Two reporters (spec “several users”) |
| D11 | P2 | Exclude a `###` block (omit from execute, Problems stay ingested) |
| D12 | P2 | Non-owner `/compile`; group commands/documents are not Problems |
| D13 | P2 | Wrong `SINCE` / empty period; supersede leftover pending run |
| D14 | P2 | Invalid upload fail-closed; second Confirm does not duplicate creates |
| D15 | P2 | Uncertain leftover (B2) appears on a later `/compile` |
| D16 | P3 | Do not add RAG, live tracker reads, skip-comments, or GitLab for this demo |
| D17 | P3 | Paid/hosted LLM and GPU are optional; not required if a fast local/API model works |

---

## Model latency (D1, D4, D5, D8, D17)

### D1 — Try other models — P0

**Why.** Host `qwen3:8b` produces the right A/B shape, but match often takes several minutes and needed a **900s** read timeout. A short demo cannot sit on a spinner. OpenRouter `gpt-oss-20b:free` already 429’d; do not bet the slot on that free tier.

**Do.** Config-only swap (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). Same OpenAI-compatible client; no architecture change. Time **cluster + match** separately. Accept a model only if:

- JSON validates (partition, closed-world identities) with at most the existing one retry;
- A still clusters two export reports, Skip-matches seed `#N`, Creates CRM (or you update the script the way we did after qwen);
- B still Skip + Uncertain `unknown`;
- end-to-end compile is short enough to narrate (aim **under ~60–90s**, not 10+ minutes).

Keep `qwen3:8b` as the fallback that already passed. If a faster model **changes** A/B outcomes, treat that like the qwen debug/7 and debug/9 update: fix the script and canned tests, do not force the model to “always create.”

Candidates (try in this order unless you already know the host GPU):

1. Smaller/faster local instruct models on Ollama (e.g. 7B-class without a long “thinking” trace).
2. Same model on **GPU** if the current run is CPU.
3. A **non-free** OpenRouter (or other) chat-completions model with a key in `.env` only.
4. Do **not** raise `LLM_READ_TIMEOUT` further as the demo strategy.

### D4 — Ack compile immediately — P1

**Why.** `/compile` currently sends nothing until cluster+match finish. On qwen that is minutes of dead air in the owner DM. Stakeholders may think the bot hung.

**Do.** Small handler change: send “Compile started. This can take a minute…” **before** the blocking call; keep fail-closed “Compile failed. No document sent.” If you skip the code change, the presenter **must** narrate from the **console** (`compile starting`, `match LLM call starting`).

### D5 — Smaller evidence pack — P1

**Why.** Match latency scales with prompt size (`k`, README, issue bodies in [config/demo.yaml](../config/demo.yaml)). Three tiny demo repos still send meta + README + issues on every match. Shrinking the pack is faster than a new model and does not change ports.

**Do.** Try lower `k` / `readme_max_chars` / `issue_body_max_chars`, refresh, re-time match. Stop if Skip `#N` or CRM routing degrades.

### D8 — Pre-warm, restart, script the wait — P1

**Why.** First Ollama request loads weights. Bot process still has the old timeout/model until restart. Compile runs in `to_thread`: group intake during that window queues.

**Do.** `ollama run <model>` once before the room fills. Restart `python -m tg_triage` after any `.env` change; confirm the log line with URL, model, and read timeout. Do not post new group reports during compile. Put a one-line talking point on the wait (fixtures vs live GitHub, LLM recommends / code executes).

### D17 — GPU / paid API — P3

**Why.** Nice if D1 on the laptop is still too slow. Not a product requirement.

---

## Demo world and dress rehearsal (D2, D3, D9)

### D2 — Reset GitHub + SQLite — P0

**Why.** Rehearsal already created `{GITHUB_OWNER}/crm` issue(s) and left B’s Uncertain **ingested**. Seed is **create-once** (will not rebuild). Refresh will put the extra CRM issue into the snapshot, so the next A may **Skip CRM** instead of Create. Leftover Uncertain re-enters `/compile` and the Markdown will not match the script.

**Do.** Before the real walk (and after a failed rehearsal):

1. `tg-triage-drop` (confirm) → `tg-triage-seed` → `tg-triage-refresh`.
2. Use a **fresh** `SQLITE_PATH` (or delete the demo DB file).
3. Confirm seed export issue `#N` and that CRM has **no** extra rehearsal issues.
4. Note `#N` on a cheat sheet (`Existing: …/sales-dashboard#N`).

### D3 — Timed A then B dress rehearsal — P0

**Why.** A+B succeeded once on qwen; that does not prove the **chosen fast model** plus a clean world plus today’s `SINCE`. Gate C is the live walk, not the fake tests.

**Do.** Full [demo_script.md](demo_script.md) on the demo machine: group texts, `/compile` with **today’s** date, upload, Confirm, B0 refresh, B, Confirm. Record wall-clock. Open the three GitHub repos in a browser beforehand.

### D9 — File round-trip — P1

**Why.** Option A is download → edit outside Telegram → upload. Telegram upload already timed out in development; the recovery is **resend the same file**. Phone vs laptop is why the file lives in the DM.

**Do.** Practice download, a tiny Body edit, upload. If “Upload stored” does not appear, resend. Keep the `.md` on the laptop used for `/execute`.

---

## Extra scenarios (D6, D7, D10–D15)

A+B already show cluster, Skip seed, Create CRM, Uncertain left ingested, confirm gate. They do **not** show several HITL moves the spec calls out.

### D6 — Owner edit Uncertain → Create (or visible Body edit) — P1

**Why.** Spec §19: the demo must make **human + system collaboration** visible. Live A is “keep generated Skip + Create.” That looks autonomous unless you **change the file**. The original A story (move Uncertain into Create on `crm`) is still the clearest HITL beat; B already leaves Uncertain.

**Do.** In rehearsal or on stage: move one Uncertain `###` into `## Create`, set `Repo: {GITHUB_OWNER}/crm`, add `Body`, upload. Or at minimum change a Create Body so Confirm is obviously executing **the upload**, not the LLM JSON. Fake e2e can stay on qwen’s A/B; this is a live talking point, not a required canned-test rewrite.

### D7 — Cancel — P1

**Why.** Spec: “must not demonstrate autonomous issue creation.” Cancel is the proof: preview counts, no GitHub write, “Cancelled. No GitHub writes.”

**Do.** Once in rehearsal (smoke already covers this). On stage, **say** Confirm is mandatory; optional live Cancel if the slot is long.

### D10 — Two reporters — P2

**Why.** Spec item 1 says “several users.” The script uses one Reporter. Two Telegram accounts posting A1 vs A2 makes intake look less like a single-operator loop.

### D11 — Exclude a `###` block — P2

**Why.** Legend: delete a block → not executed, Problems stay `ingested`. Shows the file is the authority without extra product.

### D12 — Allowlist and intake filters — P2

**Why.** Non-owner `/compile` must no-op. Group `/compile`, documents, and bot commands must not become Problems. Cheap trust for “this is not a chatty agent.”

### D13 — `SINCE` and supersede — P2

**Why.** Wrong date → “No ingested problems in that period.” A second `/compile` supersedes a leftover pending/awaiting run (one-line warning). Easy to hit if a previous compile is still sitting in DM.

### D14 — Fail-closed upload and idempotent Confirm — P2

**Why.** Unknown `##` or Create with `Repo: unknown` must reject the whole plan. Retry Confirm must not open a second GitHub issue. Good if someone asks “what if the file is wrong / we tap twice.”

### D15 — Uncertain comes back — P2

**Why.** B5 already says B2 stays `ingested` and a later `/compile` can pick it up. One extra compile after B closes the lifecycle story.

---

## What not to do before this demo (D16)

**Why.** The MVP is a closed-world, HITL Markdown loop. Adding live GitHub search, RAG, skip-comments, GitLab, or an in-bot editor reopens given-up scope and burns the remaining calendar.

**Do not:**

- Raise timeouts instead of switching model (D1).
- Point `config/demo.yaml` at real company repos.
- Refresh mid-compile or skip B0 and hope Skip still hits `#N`.
- Commit `.env`, tokens, or `fixtures/github/<live-owner>/`.
- Change canned A/B **unless** the demo model’s live shape actually changed (then update script + tests the same way as qwen).

---

## Suggested order if time is short

1. **D2** reset world.
2. **D1** pick a fast model that still validates.
3. **D8** pre-warm + restart; **D3** timed A+B.
4. **D6** one visible owner edit; **D7** Cancel once.
5. **D4/D5** only if compile is still too slow or too silent.
6. **D9–D15** as time allows.

Backup if live LLM dies on the day: show a **rehearsal** `triage-run-*.md` and walk parse → Confirm → GitHub URL, and say compile failed closed (no document / no writes). That preserves “LLM recommends / code executes” better than pasting JSON into GitHub by hand.
