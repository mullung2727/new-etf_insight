# daily-us-facts

## Purpose

SEC XBRL 재무 원본(companyfacts)을 `D:/sec_edgar/` 에 회사별로 보관하고,
정기보고서(10-K·10-Q·20-F·40-F 등) 전 계정을 "값이 바뀐 공시만" 버전으로 `<데이터루트>/db/us_facts.sqlite3` (<데이터루트> = .env NEW_ETF_DATA_ROOT, 현재 E:\new-etf_insight_data — data_paths.db_dir()) 에 쌓는다.
상장폐지 회사 포함. 설계 `docs/done/PLAN_US_FACTS.md`.

## Execution

- Windows 작업: `\new-etf_insight\daily-us-facts`, 매일 04:00, 한도 1시간.
- Python: `etl/scripts/build_us_facts.py` (인자 없음 = 일일 갱신).
- PowerShell 러너: `ops/scheduled-tasks/run-us-facts.ps1`. 로그 `etl/logs/us-facts-yyyyMMdd.log`.
- SEC 일일 공시 목록(`daily-index/form.YYYYMMDD.idx`, KST 다음날 11:02 게시)에서 정기보고서 낸 CIK 만 companyfacts 재수집.
  미국 D일 공시는 KST D+2일 04:00 에 반영.
- 직전 3일치 목록의 CIK 도 다시 받는다(공시 직후 companyfacts 반영 지연 대비). 재처리는 멱등.
- `.env` `SEC_USER_AGENT` 필수. 호출 간격 0.12초.

## Completion and recovery

- `state.last_index_date` 가 마지막으로 처리한 목록 날짜. 실행 중 하나라도 실패하면 전진하지 않고 다음 실행이 같은 날짜부터 재처리.
- 목록 404: 주말 또는 4일 이상 지난 평일 → 휴일로 넘김. 그보다 최근 평일 → 미게시로 보고 그날 중단.
- PC 가 며칠 꺼져 있어도 다음 실행이 밀린 날짜를 전부 처리.
- D: 미연결이면 exit 1.
- 성공 시 알림 없음. 실패 시에만 Discord `batch` 채널.

## First load (1회, 수동)

```powershell
cd etl
uv run python scripts/build_us_facts.py --init-from-zip D:/sec_edgar/bulk/companyfacts.zip
```

`companyfacts.zip`(약 1.4GB, https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip) 을 먼저 받아둔다.
끝나면 `last_index_date` = zip 수정일 − 3일. 성공 확인 후 zip 삭제.

이어서 업종 분류(파마프렌치 8종)와 회사별 현재 SIC(companies 전체, 약 1시간):

```powershell
uv run python scripts/build_us_facts.py --load-ff --load-sic
```

일일 갱신은 companyfacts 를 받은 회사의 SIC 도 같이 갱신한다.

## Manual checks

```powershell
Get-Content etl\logs\us-facts-$(Get-Date -Format yyyyMMdd).log -Tail 5
```
