from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _decode_best_effort(b: bytes | None) -> str:
    if not b:
        return ""
    try:
        return b.decode("utf-8")
    except Exception:
        try:
            return b.decode("cp949")
        except Exception:
            return b.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--search", required=False, default="false")
    parser.add_argument("--agent-timeout", type=int, default=600)
    args = parser.parse_args()

    prompt = sys.stdin.read()
    # subprocess stdin decoding 과정에서 surrogate 문자가 섞일 수 있어 정규화
    prompt = prompt.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        prompt_path = Path(td) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        message = (
            "Read the prompt file and output ONLY valid JSON (no markdown, no explanation). "
            f"Prompt file: {prompt_path.as_posix()}. "
            + (f"Schema file: {Path(args.schema).as_posix()}. " if args.schema else "")
            + "Return exactly one JSON object."
        )

        cmd = [
            "openclaw.cmd",
            "agent",
            "--json",
            "--agent",
            "main",
            "--timeout",
            str(args.agent_timeout),
            "--message",
            message,
        ]

        proc = subprocess.run(cmd, capture_output=True, text=False, timeout=args.agent_timeout + 60)
        stdout = _decode_best_effort(proc.stdout)
        stderr = _decode_best_effort(proc.stderr)

        if proc.returncode != 0:
            sys.stderr.write(stderr or stdout)
            return proc.returncode

        data = json.loads(stdout.strip())
        payloads = data.get("payloads") or data.get("result", {}).get("payloads", [])
        if not payloads:
            raise RuntimeError(f"No payloads returned from openclaw agent; keys={sorted(data.keys())}")
        text = (payloads[0].get("text") or "").strip()
        parsed = json.loads(text)

        out_path.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
