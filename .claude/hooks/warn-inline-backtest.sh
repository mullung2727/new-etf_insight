#!/usr/bin/env bash
# 목적: 즉석 백테스트 계산(일봉+분봉) 감지 시 research/backtest_* 사용 여부 경고.
# 경고만(차단 아님). 배경: 2026-10-01 D+1 즉석 계산에서 상한가 가드·비용 기준 누락 사건.
# Bash("tool_name":"Bash"일 때만): python 인라인(-c / - 뒤가 공백·끝·< / heredoc <<) + 키워드. -m은 트리거 아님.
# Write|Edit("tool_name":"Write|Edit"일 때만): .py 내용에 키워드 (공용 모듈·저장소 안 etl/ 제외).
# ETL 제외는 저장소 안 etl/ 만: new-etf_insight[/\\]+etl[/\\] 또는 상대경로 ^etl[/\\].
#   (루트 절대경로에 \etl\ 이 항상 들어가므로 (^|[/\\])etl[/\\] 은 모든 파일을 제외해버림)
# jq 비의존(Git Bash에 jq 없음) — stdin 전체를 sed/grep, 출력은 printf.
input=$(cat)
[ -z "$input" ] && exit 0

tool_name=$(printf '%s' "$input" | sed -n -E 's/.*"tool_name"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' | head -n 1)

has_minute=0
has_daily=0
if printf '%s' "$input" | grep -Eq 'minute_bars|day_bars|backtest_minute|mharness|mclean'; then
  has_minute=1
fi
if printf '%s' "$input" | grep -Eq 'load_px|krx_ohlcv|backtest_daily'; then
  has_daily=1
fi
if [ "$has_minute" -eq 0 ] && [ "$has_daily" -eq 0 ]; then
  exit 0
fi

trigger=0

# Bash: python 인라인 실행(python -c / python - 뒤가 공백·끝·< / heredoc <<) + 키워드. -m 제외
if [ "$tool_name" = "Bash" ]; then
  if printf '%s' "$input" | grep -q 'python'; then
    if printf '%s' "$input" | grep -Eq 'python[0-9.]*(\.exe)? +-c([[:space:]]|$)|python[0-9.]*(\.exe)? +-([[:space:]]|$|<)|<<'; then
      trigger=1
    fi
  fi
fi

# Write|Edit: file_path가 .py + 내용에 키워드. 공용 모듈·저장소 안 etl/ 경로는 제외
if [ "$tool_name" = "Write" ] || [ "$tool_name" = "Edit" ]; then
  file_path=$(printf '%s' "$input" | sed -n -E 's/.*"file_path"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' | head -n 1)
  if printf '%s' "$file_path" | grep -Eq '\.py$'; then
    if printf '%s' "$file_path" | grep -Eq 'research[/\\]+backtest_daily[/\\]|research[/\\]+backtest_minute[/\\]|new-etf_insight[/\\]+etl[/\\]|^etl[/\\]'; then
      : # 제외 경로 — Write|Edit 분기로 트리거 안 함
    else
      rest=$(printf '%s' "$input" | sed -E 's/"file_path"[[:space:]]*:[[:space:]]*"[^"]*"/"file_path":""/')
      if printf '%s' "$rest" | grep -Eq 'minute_bars|day_bars|backtest_minute|mharness|mclean|load_px|krx_ohlcv|backtest_daily'; then
        trigger=1
      fi
    fi
  fi
fi

if [ "$trigger" -eq 0 ]; then
  exit 0
fi

msg_minute='즉석 분봉 계산 감지: research/backtest_minute 사용했나? 전일 정보 prevday.attach_prev, 유니버스 data.universe(전일 정보만), 지정가 fills.limit_buy_fill(pre_open= 명시), 09:00 가격 data.snapshot, 청산 exits.tp_sl_exit. 통계·비용은 backtest_daily stats(COSTS 기준 0.35%, day_*). research/BACKTEST_DATA.md 맨 위 체크리스트 참고.'
msg_daily='즉석 일봉 계산 감지: research/backtest_daily 사용했나? 상한가 guards.limit_up_close/limit_up_open, 비용 stats.COSTS(기준 0.35%), 일별가중 stats.day_*. research/BACKTEST_DATA.md 맨 위 체크리스트 참고.'
msg=''
if [ "$has_minute" -eq 1 ]; then
  msg="$msg_minute"
fi
if [ "$has_daily" -eq 1 ]; then
  if [ -n "$msg" ]; then
    msg="$msg $msg_daily"
  else
    msg="$msg_daily"
  fi
fi

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"'"$msg"'"}}'
exit 0

# 확인용 예시 입력 5개:
# 1. Bash `uv run python - <<'EOF' ... minute_bars ...` → 분봉 경고
#    {"tool_name":"Bash","tool_input":{"command":"uv run python - <<'EOF'\nfrom research.backtest_minute import minute_bars\nEOF"}}
# 2. Write `C:/.../scratchpad/x.py` 내용 `load_px` → 일봉 경고
#    {"tool_name":"Write","tool_input":{"file_path":"C:/repo/scratchpad/x.py","content":"from research.backtest_daily import load_px"}}
# 3. Write `research/backtest_minute/data.py` → 경고 없음
#    {"tool_name":"Write","tool_input":{"file_path":"research/backtest_minute/data.py","content":"import minute_bars"}}
# 4. Bash `uv run python -m unittest discover -s ../research/backtest_daily/tests` → 경고 없음 (-m은 트리거 아님)
#    {"tool_name":"Bash","tool_input":{"command":"uv run python -m unittest discover -s ../research/backtest_daily/tests"}}
# 5. Write 절대경로 `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\research\private\x.py` 내용 `load_px` → 일봉 경고 (루트의 \etl\ 은 제외 아님)
#    {"tool_name":"Write","tool_input":{"file_path":"C:\\Users\\mullu\\.openclaw\\workspace\\etl\\new-etf_insight\\research\\private\\x.py","content":"from research.backtest_daily import load_px"}}
