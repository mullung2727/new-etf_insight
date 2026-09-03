#!/usr/bin/env bash
# PreToolUse(Bash) gate: main 브랜치 직접 커밋 금지 -> 브랜치 + PR.
# CodeRabbit 은 PR 이벤트로만 돌기 때문에 main 직접 커밋은 리뷰 없이 들어간다.
# 하니스가 실행하므로 모델 망각과 무관하게 작동. jq 비의존(Git Bash에 jq 없음).
#
# 차단: 현재 브랜치가 main 인 상태의 `git commit`(rtk 접두 포함).
# 예외(허용): 명령에 ALLOW_MAIN_COMMIT=1 을 붙인 경우 — 사용자가 명시적으로 승인한 커밋.
input=$(cat)
[ -z "$input" ] && exit 0

printf '%s' "$input" | grep -q 'git[[:space:]]\+commit' || exit 0
printf '%s' "$input" | grep -q 'ALLOW_MAIN_COMMIT=1' && exit 0

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$branch" = "main" ] || exit 0

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"main 직접 커밋 금지 -> 브랜치 + PR (CodeRabbit 은 PR 에서만 리뷰한다). 브랜치 따기 전 git log origin/main..main 으로 안 올라간 커밋부터 확인할 것. 절차는 skills/new-etf-insight-git-workflow/SKILL.md. 사용자가 승인한 예외는 ALLOW_MAIN_COMMIT=1 을 명령에 붙일 것."}}'
exit 0
