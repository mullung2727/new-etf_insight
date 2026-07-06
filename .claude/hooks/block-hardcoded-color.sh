#!/usr/bin/env bash
# PreToolUse(Write|Edit) gate: broker-web UI에서 색 하드코딩 금지 → semantic 토큰 강제.
# DESIGN.md 적용 유지 목적. 하니스가 실행하므로 모델 망각과 무관하게 작동.
# jq 비의존 — stdin 전체를 grep. 스코프/패턴만 검사(정밀 JSON 파싱 X).
#
# 스코프: broker-web/{app,components}/**/*.tsx|ts 대상 편집만.
#   → globals.css(토큰 정의처, .css라 제외), lib/tokens.ts(차트 색 resolver, lib이라 제외) 자연 허용.
# 차단 패턴:
#   1) arbitrary-hex 클래스   text-[#abc] bg-[#0A1628] 등
#   2) 네임드 팔레트색         bg-blue-600 text-red-500 등 (→ 시맨틱/buy·sell 토큰 사용)
#   3) 인라인 style hex        "#rrggbb" (→ var(--color-*) 또는 getChartTokens())
# ponytail: hex 탐지는 #6자리 단순매칭 — SHA/ID 오탐 가능(천장). tsx UI 파일 한정이라 실무상 무해.
input=$(cat)
[ -z "$input" ] && exit 0

# 스코프 밖이면 통과
printf '%s' "$input" | grep -Eiq 'broker-web[\\/]+(app|components)[\\/][^"]*\.tsx?' || exit 0

viol=""
printf '%s' "$input" | grep -Eq '(text|bg|border|ring|fill|stroke|from|to|via)-\[#[0-9a-fA-F]{3,8}\]' && viol="arbitrary-hex 클래스 (text-[#..])"
printf '%s' "$input" | grep -Eq '(text|bg|border|ring|from|to|via|fill|stroke)-(red|blue|green|emerald|amber|yellow|orange|slate|gray|zinc|neutral|indigo|violet|purple|pink|rose|cyan|teal|sky)-[0-9]{2,3}' && viol="네임드 팔레트색 (bg-blue-600 등)"
printf '%s' "$input" | grep -Eq '#[0-9a-fA-F]{6}' && viol="인라인 style hex (#rrggbb)"

if [ -n "$viol" ]; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"broker-web UI 색 하드코딩 금지 ('"$viol"'). semantic 토큰 사용: 클래스 bg-primary/text-fin-gold/bg-buy·bg-sell, 인라인은 var(--color-*), 차트 canvas는 lib/tokens.ts getChartTokens(). 토큰 자체 정의는 globals.css/lib/tokens.ts에서."}}'
  exit 0
fi
exit 0
