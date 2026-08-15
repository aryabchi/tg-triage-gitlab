# GitHub Issues
# run: 7
# since: 2026-08-13

## Legend

Section is the action. Move a ### block to change outcome.
Delete a ### block to exclude it from this run (Problems stay ingested).

Create — must: Repo (owner/repo, not unknown), Problems, Body
         optional: Priority
         execute: create a GitHub issue (title + body only); Problems become linked

Skip — must: Repo, Existing (owner/repo#n), Problems
       optional: Body (plan note only; not written to GitHub), Priority
       execute: record Existing; no GitHub write; Problems become linked

Uncertain — must: Problems, Reason
            optional: Repo (may be unknown), Priority
            execute: nothing; Problems stay ingested

Suggested Priority values (optional; Markdown-only; not sent to GitHub).
Missing, unset, or non-suggested values do not fail the plan:
- P1 — user-facing broken, no workaround
- P2 — degraded, or a workaround exists
- P3 — minor, cosmetic, or nice-to-have

## Create

### Sales dashboard export hangs
- Repo: acme/sales-dashboard
- Problems: #101, #102
- Priority: P1
- Body: |
    Sales dashboard export never finishes (spinner).
    Reported by 2 users.

## Skip

### Login timeout already filed
- Repo: acme/sales-dashboard
- Existing: acme/sales-dashboard#81
- Problems: #104
- Body: |
    Same as already filed issue #81.

## Uncertain

### Customer synchronization is delayed
- Repo: unknown
- Problems: #103
- Reason: Could be acme/crm or acme/customer-portal
