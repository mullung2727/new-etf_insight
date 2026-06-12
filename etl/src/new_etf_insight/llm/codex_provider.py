from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class CodexProvider:
    def generate_json(
        self,
        prompt: str,
        *,
        output_schema_path: Path,
        search: bool = False,
    ) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "codex_last_message.txt"

            command = ["codex.cmd" if os.name == "nt" else "codex"]
            if search:
                command.append("--search")

            command.extend(
                [
                    "-a",
                    "never",
                    "exec",
                    "--sandbox",
                    "read-only",
                    "--output-schema",
                    str(output_schema_path),
                    "--output-last-message",
                    str(output_path),
                    "-",
                ]
            )

            subprocess.run(
                command,
                check=True,
                text=True,
                input=prompt,
                encoding="utf-8",
                errors="replace",
            )

            return output_path.read_text(encoding="utf-8").strip()
