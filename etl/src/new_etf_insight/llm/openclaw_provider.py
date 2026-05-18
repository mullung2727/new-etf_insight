from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


class OpenClawProvider:
    def generate_json(
        self,
        prompt: str,
        *,
        output_schema_path: Path,
        search: bool = False,
    ) -> str:
        command_template = os.getenv("OPENCLAW_LLM_COMMAND", "").strip()
        if not command_template:
            raise RuntimeError("OPENCLAW_LLM_COMMAND is required when ETF_LLM_PROVIDER=openclaw")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "openclaw_last_message.txt"
            command_text = command_template.format(
                output_schema_path=str(output_schema_path),
                output_path=str(output_path),
                search="true" if search else "false",
            )
            command = shlex.split(command_text)

            subprocess.run(
                command,
                check=True,
                input=prompt,
                text=True,
            )

            return output_path.read_text(encoding="utf-8").strip()
