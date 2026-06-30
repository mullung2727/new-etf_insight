#!/usr/bin/env bash
# PreToolUse(Write|Edit) gate: .env 는 repo 최상위에서만 관리.
# 메모리 project_env_root_only. 하위 폴더(api/, broker/ ...)에 .env 생성/수정 차단.
# 과거 반복 삽질: api/.env 의 상대경로가 PROJECT_DIR 기준 join 으로 etl 중복 → 500.
# config 는 root .env 로 fallback 하므로 하위 .env 자체가 불필요.
#
# 차단: basename 이 정확히 `.env` 인 비-root 경로.
# 허용: root `.env`, 그리고 `.env.local`/`.env.example` 등 접미사 붙은 파일(Next.js 등).
# jq 비의존 — stdin 을 sed 로 파싱, 출력은 printf.
input=$(cat)
[ -z "$input" ] && exit 0

# file_path 추출 (경로엔 따옴표 없음 가정). JSON 이스케이프 \\ → / 정규화.
fp=$(printf '%s' "$input" | sed -nE 's/.*"file_path"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p')
[ -z "$fp" ] && exit 0
norm=$(printf '%s' "$fp" | sed -E 's#\\\\#/#g; s#\\#/#g')

# basename 이 정확히 .env 가 아니면 무관 (.env.local 등 통과)
base=${norm##*/}
[ "$base" = ".env" ] || exit 0

# root .env 판정: PROJECT_DIR/.env 또는 상대 ".env"
proj=$(printf '%s' "${CLAUDE_PROJECT_DIR:-}" | sed -E 's#\\\\#/#g; s#\\#/#g; s#/$##')
if [ "$norm" = ".env" ] || [ "$norm" = "./.env" ] || { [ -n "$proj" ] && [ "$norm" = "$proj/.env" ]; }; then
  exit 0
fi

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":".env 는 repo 최상위에서만 관리 (메모리 project_env_root_only). 하위 폴더 .env 금지 — config 가 root .env 로 fallback 하고, 디폴트 경로가 정상. .env.local/.env.example 은 허용."}}'
exit 0
