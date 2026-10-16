#!/usr/bin/env bash
# PreToolUse(Bash|PowerShell) gate: worktree 안 junction/심볼릭 링크 사고 방지.
# 2026-10-16 사고: worktree 안 etl/db 가 메인 실DB 를 가리키는 junction 이었고,
# git worktree remove --force 가 링크를 따라 들어가 메인 실DB 를 지웠다.
# 하니스가 실행하므로 모델 망각과 무관하게 작동. jq 비의존(Git Bash에 jq 없음).
# stdin = 도구 입력 JSON 전체, grep/sed 로만 파싱, deny 는 printf 로 JSON 출력.
#
# 차단 1: 링크 생성 — `mklink /J|/D`, `New-Item -ItemType Junction|SymbolicLink`, `ln -s[f]`.
# 예외(허용): 명령에 ALLOW_LINK=1 을 붙인 경우 — 사용자가 명시적으로 승인한 링크 생성.
# 차단 2: 링크가 남아 있는 worktree 의 `worktree remove` — find -type l 로 검사 후 deny.
# 예외: 없음(ALLOW 키워드 없음) — 링크만 먼저 제거 후 다시 remove.
# 차단 3: `.claude/worktrees` 경로 재귀 삭제 — rm -r[f], Remove-Item -Recurse, rmdir|rd /s.
# 예외: 없음 — git worktree remove 로(차단 2 검사 통과 후).
input=$(cat)
[ -z "$input" ] && exit 0

# 1. 링크 생성 차단
if ! printf '%s' "$input" | grep -q 'ALLOW_LINK=1'; then
  link_cmd=0
  if printf '%s' "$input" | grep -qi 'mklink' \
    && printf '%s' "$input" | grep -qE '/[JjDd]([[:space:]"'"'"']|$)'; then
    link_cmd=1
  fi
  if printf '%s' "$input" | grep -qi 'new-item' \
    && printf '%s' "$input" | grep -qiE '\-itemtype[:[:space:]]+["'"'"']?(junction|symboliclink)'; then
    link_cmd=1
  fi
  printf '%s' "$input" | grep -qE '\<ln[[:space:]]+-[A-Za-z]*s' && link_cmd=1
  if [ "$link_cmd" = 1 ]; then
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"junction/심볼릭 링크 생성 금지 — worktree 정리 시 링크 너머 실데이터 삭제 사고(2026-10-16). DB 경로는 환경변수·인자로 넘길 것. 사용자 승인 시 ALLOW_LINK=1 을 명령에 붙일 것."}}'
    exit 0
  fi
fi

# 2. worktree remove 전 링크 검사
if printf '%s' "$input" | grep -qE 'worktree[[:space:]]+remove([[:space:]]|$)'; then
  rest=$(printf '%s' "$input" | sed -E 's#.*worktree[[:space:]]+remove##')
  # remove 뒤 인자 중 -로 시작하지 않는 첫 토큰이 경로. 플래그(--force 등)는 건너뛴다.
  rest=$(printf '%s' "$rest" | sed -E ':a; s#^[[:space:]]+##; s#^-[^[:space:]"'"'"']+##; ta')
  rest=$(printf '%s' "$rest" | sed 's#\\"#"#g')
  case "$rest" in
    '"'*) path=$(printf '%s' "$rest" | sed -E 's#^"([^"]*)".*#\1#') ;;
    "'"*) path=$(printf '%s' "$rest" | sed -E "s#^'([^']*)'.*#\1#") ;;
    *) path=$(printf '%s' "$rest" | sed -E 's#^([^[:space:]"'"'"',;|&]+).*#\1#') ;;
  esac
  path=$(printf '%s' "$path" | sed 's#\\\\#\\#g')
  if [ -n "$path" ]; then
    cwd=$(printf '%s' "$input" | sed -nE 's#.*"cwd" *: *"([^"]*)".*#\1#p')
    cwd=$(printf '%s' "$cwd" | sed 's#\\\\#\\#g')
    case "$path" in
      ~/*) full="$HOME/${path#~/}" ;;
      [A-Za-z]:[/\\]*|[/\\]*) full="$path" ;;
      *) if [ -n "$cwd" ]; then full="$cwd/$path"; else full="$path"; fi ;;
    esac
    if [ -d "$full" ]; then
      link=$(find "$full" -type l -print -quit 2>/dev/null)
      if [ -n "$link" ]; then
        jpath=$(printf '%s' "$link" | sed -e 's#\\#\\\\#g' -e 's#"#\\"#g')
        printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"worktree 안에 링크 있음: '"$jpath"' — 먼저 링크만 제거(cmd /c rmdir \"'"$jpath"'\", /s 금지) 후 다시 remove. --force 는 링크 너머를 지운다(2026-10-16 사고)."}}'
        exit 0
      fi
    fi
  fi
fi

# 3. worktree 폴더 재귀 삭제 차단
if printf '%s' "$input" | grep -qE '\.claude[/\\][\\/]*worktrees'; then
  rec_del=0
  printf '%s' "$input" | grep -qE '\<rm([[:space:]]+-[A-Za-z]+)*[[:space:]]+-[A-Za-z]*[rR]' && rec_del=1
  if printf '%s' "$input" | grep -qi 'remove-item' \
    && printf '%s' "$input" | grep -qiE '\-recurse'; then
    rec_del=1
  fi
  printf '%s' "$input" | grep -qiE '\<(rmdir|rd)[[:space:]]+/s' && rec_del=1
  if [ "$rec_del" = 1 ]; then
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"worktree 폴더 재귀 삭제 금지 — git worktree remove 로(링크 검사 hook 통과 후)."}}'
    exit 0
  fi
fi
exit 0
