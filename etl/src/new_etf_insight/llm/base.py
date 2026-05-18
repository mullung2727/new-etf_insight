from __future__ import annotations

from pathlib import Path
from typing import Protocol


class LlmProvider(Protocol):
    def generate_json(
        self,
        prompt: str,
        *,
        output_schema_path: Path,
        search: bool = False,
    ) -> str:
        ...
