#!/usr/bin/env bash
# PreToolUse(Bash) gate: etl 프로젝트는 맨몸 python 금지 → `uv run python` 강제.
# 메모리 feedback_use_uv_python. 하니스가 실행하므로 모델 망각과 무관하게 작동.
# jq 비의존(Git Bash에 jq 없음) — stdin 전체를 sed/grep, 출력은 printf.
#
# 차단: bare `python` / `python3` / `python3.13` / `python.exe` 실행.
# 예외(허용): `uv run python`, 경로 포함 `.venv/Scripts/python(.exe)`(운영 스케줄러),
#            `py -N.N`(Windows 런처, 별도 바이너리), `.claude/skills/` 스탠드얼론 스크립트.
input=$(cat)
[ -z "$input" ] && exit 0

# 허용 형태를 placeholder(@OK@)로 치환해 시야에서 제거
scrubbed=$(printf '%s' "$input" \
  | sed -E 's#uv run python#@OK@#g' \
  | sed -E 's#py -[0-9]+\.[0-9]+#@OK@#g' \
  | sed -E 's#[^ "]*\.venv[/\\]+[Ss]cripts[/\\]+python(\.exe)?#@OK@#g' \
  | sed -E 's#(PYTHONIOENCODING=[^ ]+ +)?python[0-9.]* +[^ "]*\.claude[/\\]+skills[/\\]+[^ "]+#@OK@#g')

# 잔존 bare python 토큰 탐지 (명령 시작·구분자·할당·따옴표 뒤 단독 python)
if printf '%s' "$scrubbed" | grep -Pq '(^|[[:space:];&|()`="\x27])python([0-9.]*|\.exe)?([[:space:]"\x27]|$)'; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"etl 프로젝트 맨몸 python 금지 -> `uv run python` 사용 (메모리 feedback_use_uv_python). 예외: .venv\\Scripts\\python.exe(운영), py 런처, .claude/skills 스크립트."}}'
  exit 0
fi
exit 0
