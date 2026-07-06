# Telegram LangGraph Analysis Plan (B — LLM 추출)

> **상태**: 구현 완료(코드+테스트). 이 문서는 실제 구현
> `etl/scripts/telegram_langgraph/telegram_analysis_langgraph.py`를 반영한다.

## 목적

- `etl/db/telegram_public.sqlite3`에 수집된 텔레그램 글을 **배치 사이 증분 단위**로 분석한다.
- 핵심: 이번 배치 새 글에서 **중요하게·반복적으로 부각된 종목**을 뽑고, 과거 7일 대비
  무엇이 달라졌는지(신규/지속+변화) 서술해 종목코드별로 DB에 저장한다.
- LLM 호출은 기존 방식대로 `new_etf_insight.llm.generate_json()`(provider=codex)을 쓴다.

### 하루 3회 배치 · 증분

- **10:05 / 16:05 / 익일 00:05 (KST)**, session 라벨 = `morning` / `close` / `evening`.
- 증분 경계 = **post_id 워터마크**. 각 run은 직전 run 이후 새 글만 본다.
- 저장은 `telegram_stock_insights`에 `(date_kst, session, ticker)` 단위 upsert. **점수화 없음.**

## 추출 방식 — B (LLM 추출)

> **왜 B인가 (2026-07-03 실측으로 결정):** 처음엔 알고리즘 추출(전종목 이름 substring +
> 6자리 코드 정규식 + 마스터 검증)을 썼다. 실측 결과 260글에서 **155종목**이 잡혔고
> 대부분 오탐이었다 — 짧은 종목명이 다른 단어 안에서 매칭(`태양`←"태양광", `테스`←"테스트").
> 마스터 검증은 이걸 못 막는다(`태양`·`테스`는 실재 종목코드라 통과). 게다가 이력 없는
> 오탐이 **가짜 `new`(신규 부각) 종목**으로 저장돼 시그널을 오염시킨다.
>
> → 추출을 LLM에 맡긴다. LLM이 원문을 읽고 **중요·반복 언급 종목만** 정밀 추출(종목명만).
> 파이썬이 마스터로 이름→코드 확정(환각 방어) + 채널·언급수 집계(결정론). 같은 07-03에
> B로 돌리니 **8종목**(삼성전자·SK하이닉스·NH투자증권 등), 오탐 0, 환각 0.
>
> **역할 분담**: LLM = "어떤 종목"(정밀 판단). 파이썬 = 코드 확정·집계·저장(결정론적 숫자).

## LLM 콜 = 2개

1. **추출** (`stock_extract.md`) — 원문 → 중요 종목명 리스트(+note).
2. **변화판단** (`stock_insight.md`) — 종목별 [이번 언급] + [7일 이력] → change_type/서술.

> 후속 질문 추천은 **다음 계획으로 미룸**(이 파이프라인 핵심 아님). 중요도 필터링(몇 개,
> 임계값)도 다음 계획.

## 재사용한 기존 자산

- `scripts/stock_names.py` `load_name_to_code` — 전종목 이름→코드 마스터(krx_ohlcv.duckdb).
- `scripts/discover_telegram_stock_candidates.py` `aggregate_candidates` — 확정 종목의
  채널·post_ref·언급수 집계(확정 이름만 넘겨 substring 안전).
- `scripts/telegram_stock_insights.py` — `ensure_schema`/`upsert_candidate`/`update_analysis`
  (v2에서 `session` 키 추가).
- `scripts/telegram_channels.py` `load_discovery_channels` — `feed_role=discovery_source` 필터.

## 그래프 흐름 (10 노드, 선형)

```text
START
-> load_posts               # 워터마크 이후 증분(discovery 채널)만, PRAGMA query_only=ON
-> make_extract_prompt       # [채널] 본문 블록 조립(글당 500자 컷)
-> call_extract_llm          # LLM: 중요 종목명 추출 (빈 입력이면 codex 스킵)
-> parse_extract             # 이름→코드 확정(마스터) + 파이썬 집계 → stock_mentions
-> load_stock_history        # 후보별 과거 7일 telegram_stock_insights 조회
-> make_stock_insight_prompt # [이번 언급(샘플 본문)] + [7일 이력]
-> call_stock_insight_llm    # LLM: change_type/change_summary/themes/evidence (빈 입력 스킵)
-> parse_stock_insight
-> build_final_report        # 숫자=파이썬 / 서술=LLM, code로 조인. 환각 종목 drop+warning
-> persist_and_advance       # insights upsert + 워터마크 전진 (1 트랜잭션)
-> END
```

**빈 입력 가드**: `rows`가 0이면 이후 전 노드 no-op, LLM 콜 없음, 워터마크도 안 밀림.
후보 0이면 stock LLM 스킵, 워터마크만 전진(글은 봤으므로).

## 노드 요약

- **load_posts** — 워터마크(`telegram_analysis_watermark`) 읽고, `WHERE date_kst=?` 로드 후
  파이썬에서 `post_id > 워터마크[channel]` + discovery 채널 필터. 읽기 전용 커넥션.
- **make_extract_prompt / call_extract_llm** — 원문 블록 → `stock_extract_schema.json`
  (`stocks[]: {name, note}`). LLM은 **종목명만**, 코드는 시스템이 매김.
- **parse_extract** — LLM 이름을 마스터로 코드 확정(없으면 `warnings`에 `llm_name_not_in_master:` +
  drop). 확정 종목에 한해 `aggregate_candidates`로 채널·post_ref·언급수 집계. `note`는
  `discovery_reason`으로.
- **load_stock_history** — `telegram_stock_insights`에서 ticker별 `date_kst>=오늘-7 AND <오늘`.
  세션 시간순 정렬(morning→close→evening). 이력 없으면 신규.
- **make/call/parse_stock_insight** — 종목별 이번언급(샘플 최대 5, min_text_length 필터)+7일이력 →
  `stock_insight_schema.json` (`change_type: new|continued`, change_summary, themes, evidence).
- **build_final_report** — 정량(mention_count/channels/post_count)=파이썬, 서술=LLM을 `code`로
  조인. LLM이 후보에 없는 code를 내면 drop+warning. LLM 숫자는 버림.
- **persist_and_advance** — 1 커넥션·1 트랜잭션: 후보 전부 `upsert_candidate`(analysis는 LLM
  결과 있을 때만 `update_analysis`) + 채널별 `max(post_id)` 워터마크 전진. rows 0이면 무동작.
  실패 시 롤백(재실행 안전).

## DB 테이블

### `telegram_stock_insights` (2차 결과)

`etl/db/telegram_public.sqlite3`. 키 `(date_kst, session, ticker)`. `analysis`(LLM change JSON)는
탐색 필드와 분리 upsert. `llm_scores`와 무관, **점수 없음**.

### `telegram_analysis_watermark`

`channel PK / last_post_id / updated_at`. run 끝에 처리한 `max(post_id)`로 전진(max로만).
읽기는 DDL 안 함(query_only 커넥션 호환) — 스키마는 엔트리에서 선생성.

## 입력/엔트리

```python
analyze_telegram_session(
    date_kst, session,            # morning | close | evening
    db_path=Path("db/telegram_public.sqlite3"),
    output_path=None,             # 디버그 JSON 덤프(선택). 운영 저장은 persist가 DB로
    history_days=7,
    min_text_length=30,           # 프롬프트 샘플 본문 최소 길이(노이즈 컷)
    stock_db_path="",             # 비면 krx_ohlcv.duckdb 기본
)
```

CLI:
```bash
uv run python scripts/telegram_langgraph/telegram_analysis_langgraph.py \
    --date 2026-07-03 --session close [--output report.json]
```

## 출력 (final_report)

```json
{
  "date_kst": "2026-07-03",
  "session": "close",
  "post_count": 260,
  "channel_post_counts": {"getfeed": 155, "infomarketopen": 32, ...},
  "notable_stocks": [
    {
      "name": "삼성전자", "code": "005930", "change_type": "new",
      "mention_count": 32, "channels": ["getfeed", "corevalue", ...],
      "themes": ["반도체", "메모리"],
      "change_summary": "...", "evidence_summary": "..."
    }
  ],
  "warnings": []
}
```

## 알려진 한계 (날짜 경계)

워터마크(post_id) + `date_kst=?` 필터 병용. 어떤 날 D의 evening run(익일 00:05) 이후 수집된
D-날짜 글(지연/백필)은 이후 어떤 run도 `date_kst=D`를 재조회 안 해 처리 못 함. 갭 없음은
**하루 안에서만** 보장. 실무상 수집 지연이 배치 주기보다 작아 무시.

## 시간 기준

분석 기준일=`date_kst`, 글 작성시간=`posted_at_utc`. 적재시간(`created_at`/`updated_at`)은
분석 기준 아님(디버그용).

## 프롬프트 (⚠️ 초안 — 상세는 다음 계획)

- `stock_extract.md` — 원문 → 중요·반복 종목만. "태양광의 태양" 같은 부분일치 배제 지시.
  정식 종목명 출력, 코드 금지.
- `stock_insight.md` — 종목별 이번언급+7일이력 → 신규/지속+변화 서술. 입력 종목만, 숫자 금지,
  환각 금지.

문구·few-shot·변화판단 루브릭·토큰예산은 다음 계획에서 확정.

## 테스트

`etl/tests/test_telegram_analysis_langgraph.py` (17) + `test_telegram_stock_insights.py` +
`test_telegram_analysis_watermark.py`. 커버: 증분/discovery 필터, 이름→코드 확정+환각 drop,
7일 윈도우, 빈입력 codex 스킵, 파이썬/LLM 병합, persist 멱등, 워터마크 전진, e2e(2콜).

## 실측 (2026-07-03 close, DB 복사본)

- 알고리즘 substring: **155종목**(대부분 오탐).
- B(LLM 추출): **8종목**(삼성전자 32언급/4채널, SK하이닉스, NH투자증권, 신한지주, 삼성증권,
  LG전자, 한온시스템, 진성티이씨). 오탐 0, 환각 0(`warnings: []`).
- 첫 run이라 전부 `change_type=new`(이력 없음). 변화판단 가치는 다음 run부터.

## 남은 것

- ops cron 3 job(10:05 / 16:05 / 익일 00:05) 등록. 수집 배치보다 몇 분 뒤.
- **다음 계획**: 후속질문 추천, 중요도 필터링(몇 개·임계값), 프롬프트 상세 설계,
  `telegram_theme_mentions`(테마 단위).

## 확정된 결정

- 추출 = **LLM(B)**. 알고리즘 substring은 오탐 과다로 폐기.
- 마스터는 **검증**이 아니라 **이름→코드 확정**에 씀(LLM은 이름만).
- 집계 숫자(mention_count/channels)는 파이썬, 서술만 LLM.
- LLM 2콜(추출+변화판단). 질문추천·중요도필터는 다음 계획.
- persist + 워터마크 전진은 1 트랜잭션.
- 수집기(`collect_telegram_public.py`) 무변경.
- discovery 채널만 대상(getfeed/corevalue/infomarketopen/awake_realtimeCheck/kimcharger).
