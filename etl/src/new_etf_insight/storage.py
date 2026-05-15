from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            file.write("\n")
