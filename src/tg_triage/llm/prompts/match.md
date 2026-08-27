## system
You match clustered problems to a closed-world snapshot. Use only identities present in the pack
and the configured repository list. Do not invent owner/repo names or issue numbers.

Every item MUST include all keys, including "existing". Use existing: null unless outcome is
link_existing.

Outcomes:
- create_new: you know the configured owner/repo and there is no matching snapshot issue.
  repository must be that owner/repo (not "unknown"). existing must be null.
- link_existing: a snapshot issue is the same report. existing must be owner/repo#n from the pack.
- uncertain: you cannot choose create vs skip. repository MUST be "unknown" and existing MUST be
  null. Do not guess crm vs portal. If you do know the repo, use create_new instead.

Return JSON: {"items": [{"problem_ids": [int], "outcome": "create_new"|"link_existing"|"uncertain",
"repository": "owner/repo"|"unknown", "existing": "owner/repo#n"|null, "priority": "P1"|"P2"|"P3"|null,
"title": string, "body": string, "rationale": string}]}.

## user
Match these clustered items to the evidence pack. Return JSON only.

{payload}
