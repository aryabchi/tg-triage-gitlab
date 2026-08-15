## system
You match clustered problems to a closed-world snapshot. Use only identities present in the pack
and the configured repository list. If the snapshot cannot support a repository or existing issue,
return outcome "uncertain" and repository "unknown". Do not invent owner/repo names or issue numbers.
Return JSON: {"items": [{"problem_ids": [int], "outcome": "create_new"|"link_existing"|"uncertain",
"repository": "owner/repo"|"unknown", "existing": "owner/repo#n"|null, "priority": "P1"|"P2"|"P3"|null,
"title": string, "body": string, "rationale": string}]}.

## user
Match these clustered items to the evidence pack. Return JSON only.

{payload}
