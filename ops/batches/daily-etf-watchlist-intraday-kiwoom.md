# daily-etf-watchlist-intraday-kiwoom

## Schedule

Schedule, timezone, Windows Task binding, legacy OpenClaw cron metadata, and
delivery live in `ops/batches/openclaw-cron.registry.json` (source of truth).
Do not duplicate them here.

## Purpose

Run the ETF watchlist intraday Kiwoom candidate batch and immediately
score/research today's candidates.

This is the same-day pre-close watchlist batch, timed to complete LLM scoring
before the 15:19 close-bet order window. KRX OpenAPI does not reliably provide
today's full exchange data yet, so use Kiwoom `ka10030` intraday top-volume data
at 14:59 (장중 스냅샷, not final close volume) to build today's watchlist
candidates. The snapshot collector waits until 15:00, saves a `ka10001` market
snapshot for each candidate, then scores whether the D+1 open will be above the
D close and upserts that score directly into `llm_scores`.

The market snapshot step is best-effort. A time-window, authentication, network,
or per-ticker quote failure must be logged but must not block the existing scoring
and close-bet path.

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
- Before running commands in PowerShell, force UTF-8 output:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
```

- Run:

```powershell
.\.venv\Scripts\python.exe scripts\build_intraday_ranking.py --date <TODAY_YYYYMMDD>
.\.venv\Scripts\python.exe scripts\collect_watchlist_market_snapshot.py --date <TODAY_YYYYMMDD>
.\.venv\Scripts\python.exe ..\research\watchlist_expected_return\watchlist_probability_langgraph.py `
  --dates <TODAY_YYYYMMDD> --write-db `
  --reports-dir C:\Users\mullu\.openclaw\workspace\reports `
  --output-dir C:\Users\mullu\.openclaw\workspace\reports
```

`--output-dir` is required. Without it the scorer dumps
`recent_3day_probability_scores.json` next to its own source, into the tracked
research snapshot folder, and every run shows up as a repo change.

The probability scorer must use saved same-day watchlist and 15:00 snapshot rows
and upsert `etl/db/watchlist.sqlite3` `llm_scores`. It uses prior KRX rows for
`avg5_volume`, `ratio`, and previous-day market cap.

## DB-First Reporting Rule

After all three steps finish, query `etl/db/watchlist.sqlite3` directly and report from
the saved DB rows, not from stdout alone, memory, or an abbreviated
interpretation.

Query and display:

- Today's saved `watchlist(date, stock_code)` rows.
- Today's saved `intraday_ranking` rows joined with `watchlist`, so each final
  candidate has `date`, `rank`, `ticker`, `name`, `volume`, and `close`.
- Today's `watchlist_market_snapshots` rows. Report missing candidates as snapshot warnings;
  do not treat them as scoring defects. Successful rows have
  `snapshot_at`, `current_price`, `open_price`, `high_price`, `volume`,
  `change_rate`, and `source=ka10001`.
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
키움 당일 watchlist 후보 + D+1 시가 상승가능성 점수
```

Do not send only a short summary. Do not limit to only top tickers. Output every
watchlist item for today, sorted by score descending.

Build the Discord message text from the UTF-8 JSON report with the formatter
script. Do not read Korean report bodies with `Get-Content`, `type`, or any
PowerShell text pipeline, because CP949 console decoding can corrupt Korean text
before it reaches Discord.

After probability scoring completes, run:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\format_watchlist_discord_report.py `
  --json "C:\Users\mullu\.openclaw\workspace\reports\watchlist_research_<TODAY_YYYY-MM-DD>.json" `
  --db "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\db\watchlist.sqlite3" `
  --out "C:\Users\mullu\.openclaw\workspace\reports\watchlist_discord_<TODAY_YYYY-MM-DD>.json" `
  --fail-on-mojibake
```

Use only the generated `messages` array as the Discord report. If Discord length
limits are hit, send the array entries as separate messages in order. Wrap source
links in angle brackets exactly as emitted by the formatter.

For production Windows Task runs, send the generated `messages` array through
`scripts/send_report_messages.py`, which sends to the channel set by
`NOTIFY_CHANNEL` (default discord via `DISCORD_WEBHOOK_URL`; see notify.py).

For each item, the formatter displays DB-backed fields:

- 종목명 `(ticker)`
- 키움 rank
- 거래량: `today_volume`, 5일 평균 대비 `ratio` or 산출 불가
- 거래대금: `trading_value`
- 종가: `close`
- 분류: `category`
- D+1 시가 상승가능성 점수: `score`점
- 상승가능성 근거: `reason_summary`
- 뉴스 근거: `evidence_news`
- 텔레그램 근거: `evidence_web`
- 판단: `final_opinion`, including positive/negative factors and confidence
- 뉴스·텔레그램 source links from `sources`

After listing all items, include report path, DB upsert status, and Notion status
if attempted. If Discord length limits are hit, split the report into multiple
messages rather than omitting items.

On non-trading day (holiday or weekend), `build_intraday_ranking.py` detects a
휴장일 via the duplicate-snapshot guard and skips saving and candidate selection.
If휴장일 is detected, announce a concise skip message to Discord for the explicit
target date and do not run D+1 probability scoring.

On Kiwoom auth/rate-limit/API failure or scoring failure, report the exact
blocker and do not run unrelated projects.

## PowerShell Safety

Do not use Bash heredoc syntax. For multiline Python, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```
