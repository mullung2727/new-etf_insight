from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


STDERR_TAIL = 2000


class CodexProvider:
    def generate_json(
        self,
        prompt: str,
        *,
        output_schema_path: Path,
        search: bool = False,
        model: str | None = None,
    ) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "codex_last_message.txt"

            command = ["codex.cmd" if os.name == "nt" else "codex"]
            if model:
                command.extend(["-m", model])
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

            # 결과는 --output-last-message 파일로만 읽는다. codex 가 stdout 에 찍는
            # 프롬프트 전문·진행 로그는 순수 노이즈인데, 잡지 않으면 부모 stdout 으로
            # 새어 보고문을 오염시킨다(러너가 stdout 을 그대로 전송한다).
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                input=prompt,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                sys.stderr.write((result.stderr or result.stdout or "")[-STDERR_TAIL:])
                raise subprocess.CalledProcessError(
                    result.returncode, command, result.stdout, result.stderr
                )

            return output_path.read_text(encoding="utf-8").strip()
