#!/usr/bin/env bash
# PreToolUse(Bash) gate: PR 본문이 PLAN_<NAME> 을 참조하면 해당 설계문서 변경 강제.
# 하니스가 실행하므로 모델 망각과 무관하게 작동. jq 비의존(Git Bash에 jq 없음).
#
# 차단: `gh pr create` 본문(인자 또는 --body-file/-F 파일)이 PLAN_<NAME> 을 언급하는데
#   브랜치 변경분에 docs/<NAME>.md·docs/done/<NAME>.md 가 없는 경우.
# 예외(허용): 명령에 ALLOW_PLAN_PR=1 을 붙인 경우 — 문서 무관한 단순 언급.
input=$(cat)
[ -z "$input" ] && exit 0

printf '%s' "$input" | grep -q 'gh[[:space:]]\+pr[[:space:]]\+create' || exit 0
printf '%s' "$input" | grep -q 'ALLOW_PLAN_PR=1' && exit 0

body="$input"
while IFS= read -r match; do
  p=$(printf '%s' "$match" | sed -E -e 's/^[[:space:]]*--body-file[[:space:]]+//' -e 's/^[[:space:]]*-F[[:space:]]+//')
  p="${p#\"}"
  p="${p%\"}"
  if [ -n "$p" ] && [ -f "$p" ]; then
    body="$body
$(cat -- "$p" 2>/dev/null)"
  fi
done < <(printf '%s' "$input" | grep -o -E -e '--body-file[[:space:]]+("[^"]+"|[^[:space:]"]+)' -e '(^|[[:space:]])-F[[:space:]]+("[^"]+"|[^[:space:]"]+)')

names=$(printf '%s' "$body" | grep -o -E 'PLAN_[A-Z0-9_]+' | sort -u)
[ -z "$names" ] && exit 0

changed=$(git diff --name-only origin/main...HEAD 2>/dev/null)

missing=""
for n in $names; do
  printf '%s\n' "$changed" | grep -q -x -e "docs/$n.md" -e "docs/done/$n.md" || {
    if [ -z "$missing" ]; then
      missing="$n"
    else
      missing="$missing, $n"
    fi
  }
done
[ -z "$missing" ] && exit 0

printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"PR 본문이 %s 을 참조하는데 이 브랜치가 그 설계문서를 안 고쳤다. 진행 체크리스트·구현 차이 절을 갱신해 커밋할 것 (skills/new-etf-insight-plan-writing/SKILL.md). 문서 무관한 단순 언급이면 ALLOW_PLAN_PR=1 을 명령에 붙일 것."}}' "$missing"
exit 0
