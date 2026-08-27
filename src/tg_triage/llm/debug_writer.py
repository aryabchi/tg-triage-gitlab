"""Best-effort debug dump of LLM request/response JSON.

Failed writes must not fail compile. Execute must not read this directory.
"""

from __future__ import annotations

import json
from pathlib import Path


def write_debug(directory: Path, filename: str, payload: object) -> None:
    """Write ``payload`` as JSON under ``directory``. Swallow OS errors.

    ``payload`` may be any JSON-serializable value. Directory is created if
    needed.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return
