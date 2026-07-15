# Watchlist 눌림목 매수 전략 연구

## 이 폴더의 목적

- 일자별 watchlist 종목을 무조건 매수하지 않고, 눌림목이 확인된 종목만 선택적으로 매수하는 전략의 백테스트 결과를 보관한다.
- 매수 후 청산은 최장 5거래일 이내로 제한한다.
- LLM이나 사람이 이 연구를 처음 확인할 때는 이 `README.md`를 먼저 읽는다.

## 파일 안내

- `README.md`: 연구 목적, 방법, 핵심 결과, 한계와 재실행 방법을 설명하는 진입 문서다.
- `phase7_pullback_strategy.md`: 눌림목 정의별 핵심 백테스트 결과를 읽기 쉽게 정리한 문서다.
- `phase7_pullback_strategy.json`: 모든 진입·청산 조합의 수치 결과를 담은 기계 판독용 원본이다.
- `phase8_minute_pullback_strategy.md`: lower-low 후보를 1분봉 진입·청산으로 세분화한 핵심 결과다.
- `phase8_minute_pullback_strategy.json`: 분봉 규칙별 전반부 선택·후반부 검증과 비용 민감도 원본이다.
- `phase9_minute_strategy_design.md`: 캐시 완전성을 수정한 뒤 전반부에서 재설계하고 후반부 검증한 최종 결과다.
- `phase9_minute_strategy_design.json`: 312개 고정 후보 조합과 선택 전략의 상세 수치 원본이다.
- `minute_cache/`: 키움 `ka10080` 원본을 정규화한 종목·기준일별 재사용 캐시다.
- 실행 코드: `../watchlist_expected_return/phase7_pullback_strategy.py`
- 분봉 공통 캐시 코드: `../watchlist_expected_return/minute_bar_cache.py`
- 분봉 연구 코드: `../watchlist_expected_return/phase8_minute_pullback_strategy.py`
- 분봉 재설계 코드: `../watchlist_expected_return/phase9_minute_strategy_design.py`
- 회귀 테스트: `../watchlist_expected_return/tests/test_phase7_pullback_strategy.py`
- 분봉 회귀 테스트: `../watchlist_expected_return/tests/test_phase8_minute_pullback_strategy.py`
- 분봉 재설계 테스트: `../watchlist_expected_return/tests/test_phase9_minute_strategy_design.py`

## 데이터와 검증 방법

- watchlist: `etl/db/watchlist.sqlite3`
- 일봉 OHLCV: `etl/db/krx_ohlcv.duckdb`
- 분석 가능 표본: 133건
- 진입 탐색: watchlist 등록 후 D+1~D+5
- 청산: 매수 다음 거래일부터 최장 5거래일 이내
- 거래비용: 왕복 1% 차감
- 시간 검증: 2026-06-23 이전 구간에서 청산 규칙을 선택하고 이후 구간에서 별도 검증
- 동일 일봉에서 목표가와 손절가가 모두 닿으면 보수적으로 손절 우선 처리

## 비교한 눌림목 정의

- `limit_down_3pct`: watchlist일 종가보다 3% 낮은 지정가가 처음 체결될 때 매수
- `limit_down_5pct`: watchlist일 종가보다 5% 낮은 지정가가 처음 체결될 때 매수
- `gap_down_2pct`: watchlist일 종가보다 2% 이상 갭하락한 날 시가 매수
- `ma5_reclaim`: 저가가 직전 5일 종가 평균 이하로 내려갔다가 종가가 평균 이상으로 회복하면 종가 매수
- `lower_low_bullish_reversal`: 전일 저가를 이탈한 뒤 양봉으로 마감하면 종가 매수

## 핵심 결과

- 현재 표본에서 후반부 검증을 통과한 유일한 정의는 `lower_low_bullish_reversal`이다.
- 선택된 청산 규칙은 목표가 +3%, 손절가 -3%, 최장 3거래일 보유다.
- 후반부 검증 표본은 31건이다.
- 왕복 비용 1% 차감 후 후반부 평균 수익률은 +1.6779%, 승률은 54.84%다.
- 전체 watchlist 중 약 48.12%는 신호가 없어 매수하지 않는다.
- 단순 -3%/-5% 지정가와 -2% 갭하락 매수는 후반부 평균 수익률이 음수였다.

## 결론 해석

- 이 결과는 실매매 확정안이 아니라 추가 검증할 우선 후보를 찾은 결과다.
- 단순히 가격이 많이 하락했다는 이유로 매수하는 방식보다, 저가 이탈 후 양봉 반전을 확인하는 방식이 현재 표본에서는 나았다.
- 후반부 표본이 31건이고 분석 기간이 짧으므로 표본이 누적될 때마다 재실행해야 한다.
- MA와 양봉 반전 조건은 일봉 종가 확인 후 같은 종가에 체결된다고 가정한 근사치다.
- 거래정지로 OHLC가 0인 경로는 강제청산 가격을 알 수 없어 성과 표본에서 제외했다.

## 1분봉 연구 결과

- 과거 기준일 조회와 연속조회가 정상 동작했고, 캐시된 각 페이지 안에서 타임스탬프 중복은 없었다.
- 일봉 종가까지 알아야 정해지는 표본을 장중 진입에 미리 쓰지 않도록, D+1~D+5 최초 lower-low 발생일을 먼저 잡고 신호 시점 정보만 사용했다.
- 최초 phase8 캐시는 대상일 오후부터 시작하는 일부 불완전 페이지가 있어 결과를 확정값으로 사용하지 않는다.
- 캐시 완전성 기준을 대상일 장 시작 이전까지 조회하는 방식으로 수정했고, 완전한 5거래일 분봉 표본은 99건이다.
- phase9에서 312개 사전 정의 조합을 비교한 결과 `종가확정 + 추가필터 없음 + TP 3%/SL 3%/최대 3거래일`이 선택됐다.
- 비용 1% 차감 후 전반부 15건 평균 +1.5632%, 후반부 11건 평균 +3.4699%로 후반부 검증을 통과했다.
- 후반부 표본이 11건으로 작으므로 확정 전략이 아니라 추가 표본을 쌓아 재검증할 후보로 취급한다.
- TP·SL이 같은 1분봉 안에서 모두 체결 가능한 경우에는 보수적으로 SL 우선 처리했다.

## 재실행

프로젝트 루트에서 실행한다.

```powershell
etl\.venv\Scripts\python.exe -m research.watchlist_expected_return.phase7_pullback_strategy
```

- 실행하면 이 폴더의 `phase7_pullback_strategy.md`와 `phase7_pullback_strategy.json`이 갱신된다.
