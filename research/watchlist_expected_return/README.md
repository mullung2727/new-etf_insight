# Watchlist D+1 Open Probability Scoring

watchlist 기대수익률 연구와 `D+1 시가 > D 종가` 상승가능성 운영 점수를 관리한다.

## 구조

- `phase1_data_audit.py`: 운영 DB를 읽기 전용으로 감사하고 분석 가능 범위를 산출한다.
- `phase2_score_relationship.py`: 기존 점수와 D+1 시가 및 D+1~D+5 종가 수익률 관계를 분석한다.
- `phase3_rising_features.py`: D+1 시가 상승군과 비상승군의 구조화 특징을 기간 분할로 비교한다.
- `phase4_holding_strategy.py`: D+1~D+5 고정 보유와 TP/SL 전략을 시간 분할로 비교한다.
- `phase5_expected_return_model.py`: 구조화 수치 특징 기반 기대수익 shadow 모델을 walk-forward 검증한다.
- `phase6_extreme_gap_causes.py`: D일 15:00 이전 blind 정보로 D+1 극단 갭 원인 구분 가능성을 평가한다.
- `watchlist_probability_langgraph.py`: 뉴스·텔레그램·15시 시세를 참고해 D+1 시가 상승가능성 점수를 생성하고 선택적으로 `llm_scores`를 갱신한다.
- `tests/`: 임시 SQLite·DuckDB fixture를 사용하는 회귀 테스트다.
- `results/`: 단계별 JSON·Markdown 결과가 생성된다.

## 1단계 실행

프로젝트 루트에서 실행한다.

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase1_data_audit
```

다른 DB를 검사하려면 경로를 명시한다.

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase1_data_audit `
  --watchlist-db etl\db\watchlist.sqlite3 `
  --krx-db etl\db\krx_ohlcv.duckdb `
  --output-dir research\watchlist_expected_return\results
```

## 2단계 실행

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase2_score_relationship
```

## 3단계 실행

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase3_rising_features
```

## 4단계 실행

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase4_holding_strategy
```

## 5단계 실행

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase5_expected_return_model
```

## 상승가능성 LangGraph

기본 실행은 기존 `llm_scores`를 수정하지 않고 최근 3거래일을 별도 JSON으로 재평가한다.
KRX DB에 D+1 시가가 적재된 종목은 같은 결과에서 순위 AUC·Brier score도 계산한다.
운영 DB에 유효한 `watchlist_market_snapshots` 15:00 행이 있으면 현재가·시가·고가·거래량 파생지표도 사용한다.

```powershell
$env:PYTHONPATH='etl/src'
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.watchlist_probability_langgraph
```

운영 배치는 당일 날짜와 `--write-db`를 지정해 기존 `llm_scores` 행을 상승가능성 점수로 갱신한다.

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.watchlist_probability_langgraph `
  --dates <TODAY_YYYYMMDD> --write-db `
  --reports-dir C:\Users\mullu\.openclaw\workspace\reports
```

## 6단계 실행

```powershell
$env:PYTHONPATH='etl/src'
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase6_extreme_gap_causes
```
