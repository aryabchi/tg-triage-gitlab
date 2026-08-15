## system
You cluster user-reported problems. Use only the given problem ids and original texts.
Do not invent ids. Return a JSON object: {"items": [{"summary": string, "problem_ids": [int, ...]}]}.
Every provided id must appear in exactly one item.

## user
Cluster these problems. Return JSON only.

{payload}
