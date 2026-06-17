# daily-etf-watchlist-intraday-kiwoom

## Cron

- Schedule: `10 15 * * 1-5`
- Timezone: `Asia/Seoul`
- OpenClaw session target: `isolated`
- Delivery: Discord announce

## Purpose

Run the ETF watchlist intraday Kiwoom candidate batch and immediately
score/research today's candidates.

This is the same-day pre-close watchlist batch, timed to complete LLM scoring
before the 15:19 close-bet order window. KRX OpenAPI does not reliably provide
today's full exchange data yet, so use Kiwoom `ka10030` intraday top-volume data
at 15:10 (장중 스냅샷, not final close volume) to build today's watchlist
candidates. Then immediately run cause-clarity research/scoring for those
same-day candidates and upsert `llm_scores`.

The next-morning KRX confirmation job refreshes/confirms yesterday's full-market
KRX OHLCV, but same-day candidates must still get immediate LLM scores after the
Kiwoom batch.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.claude\skills\etf-watchlist-batch\SKILL.md`

## Execution

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.
- Load `.env` from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env` without printing secrets.
- Determine today's Asia/Seoul date as `YYYYMMDD`.
- Run:

```powershell
.\.venv\Scripts\python.exe scripts\build_intraday_ranking.py --date <TODAY_YYYYMMDD>
.\.venv\Scripts\python.exe scripts\run_watchlist_research.py --date <TODAY_YYYYMMDD> --skip-build
```

`run_watchlist_research.py` must use saved same-day watchlist rows and upsert
`etl/db/watchlist.sqlite3` `llm_scores`. If today's KRX OHLCV is unavailable, use
same-day `intraday_ranking` metrics for `today_volume`, `close`, and
`trading_value`, and prior KRX rows for `avg5_volume` and `ratio`.

## DB-First Reporting Rule

After both steps finish, query `etl/db/watchlist.sqlite3` directly and report from
the saved DB rows, not from stdout alone, memory, or an abbreviated
interpretation.

Query and display:

- Today's saved `watchlist(date, stock_code)` rows.
- Today's saved `intraday_ranking` rows joined with `watchlist`, so each final
  candidate has `date`, `rank`, `ticker`, `name`, `volume`, and `close`.
- Today's saved `llm_scores` joined with `watchlist`.

Use exact raw fields:

- `ratio`
- `today_volume`
- `avg5_volume`
- `trading_value`
- `close`
- `score`
- `category`
- `reason_summary`
- `final_opinion`
- `evidence_board`
- `evidence_news`
- `evidence_web`
- `sources`

Verify every today watchlist row has a joined `llm_scores` row. If a row is
missing a score, report it explicitly as a defect.

## Discord Report

Clearly label it as:

```text
키움 당일 watchlist 후보 + 즉시 LLM 스코어
```

Do not send only a short summary. Do not limit to only top tickers. Output every
watchlist item for today, sorted by score descending.

For each item, display DB-backed fields:

- 종목명 `(ticker)`
- 키움 rank
- 거래량: `today_volume`, 5일 평균 대비 `ratio` or 산출 불가
- 거래대금: `trading_value`
- 종가: `close`
- 분류: `category`
- 명확성 점수: `score`점
- 급등 원인: `reason_summary`, with concrete evidence themes from
  `evidence_news`, `evidence_web`, and `evidence_board`
- 판단: `final_opinion`, with board tone/caution if present

After listing all items, include report path, DB upsert status, and Notion status
if attempted. If Discord length limits are hit, split the report into multiple
messages rather than omitting items.

On non-trading day (holiday or weekend), `build_intraday_ranking.py` detects a
휴장일 via the duplicate-snapshot guard and skips saving and candidate selection.
If휴장일 is detected, announce a concise skip message to Discord for the explicit
target date and do not run `run_watchlist_research.py`.

On Kiwoom auth/rate-limit/API failure or scoring failure, report the exact
blocker and do not run unrelated projects.

## PowerShell Safety

Do not use Bash heredoc syntax. For multiline Python, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```

