---
name: new-etf-insight-order-lookup
description: Use when asked which strategy placed a buy/sell order for a ticker ("8001에서 278650 샀는데 무슨 건이야?", "이 주문 어느 전략?"). Scans strategy ledger tables instead of grepping logs.
---

# 주문 → 전략 역추적

로그 grep·파일 탐색부터 하지 말 것(느림, 타임아웃). 원장 DB부터 조회한다.

## 원칙

- 전략↔테이블 목록을 여기 적지 않는다. 테이블은 매번 자동 탐색 → 전략 추가·변경돼도 이 문서 수정 불필요.
- 스캔 대상은 원장 DB 2개뿐: `etl/db/watchlist.sqlite3`(전략 ledger.py 전부), `etl/db/jev_candidate.sqlite3`(Jev).
  분봉·호가 등 시세 DB는 원장이 없으므로 보지 않는다.
- 새 전략이 원장을 다른 DB에 두면 아래 `DBS` 에 추가.

## 조회

```bash
cd etl && uv run python -c "
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
CODE = sys.argv[1]
DBS = ['db/watchlist.sqlite3', 'db/jev_candidate.sqlite3']
for db in DBS:
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    for (t,) in con.execute(\"select name from sqlite_master where type='table'\"):
        cols = [c[1] for c in con.execute(f'pragma table_info(\"{t}\")')]
        k = [c for c in cols if c in ('ticker', 'code', 'symbol', 'stk_cd')]
        if not k:
            continue
        rows = con.execute(f'select * from \"{t}\" where \"{k[0]}\"=? order by rowid desc limit 3', (CODE,)).fetchall()
        for r in rows:
            print(db, t, {c: (str(v)[:60] if v is not None else None) for c, v in zip(cols, r) if v not in (None, '', 0)})
" 278650
```

## 해석

- 전략 = 테이블 이름. `*_orders` / `*_positions` 류가 실주문 원장(예: `rebound_positions`=되돌림, `close_bet_orders`=종가베팅).
- `llm_scores`, `intraday_ranking`, `*_snapshots` 등은 후보·분석 기록이지 주문 아님.
- 오늘 날짜 + 주문번호(`ord*`, `order_no`) 있는 행이 답. 계좌(:8001/:8002)는 그 전략 config 의 port 로 확인.

## 원장에서 못 찾으면

- 수동 주문이거나 원장을 DB 밖(CSV·JSON)에 쓰는 전략.
- `ops/logs/broker-*.log`(파일명=기동일, 최신 mtime 파일)에서 종목코드 → 주문번호 찾고, 그 주문번호로 다시 원장 검색.
