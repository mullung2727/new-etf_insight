# OpenClaw ETF Pipeline Guide

이 문서는 OpenClaw가 `new-etf-insight`의 ETF 일 단위 파이프라인을 실행할 때 따라야 하는 운영 가이드다.

## 기준 문서

- 작업 흐름 기준: `etl/DAILY_PIPELINE_FLOW.md`
- LLM 호출부:
  - `etl/src/new_etf_insight/llm/__init__.py`
  - `etl/src/new_etf_insight/llm/codex_provider.py`
  - `etl/src/new_etf_insight/llm/openclaw_provider.py`
- 파이프라인 진입점: `etl/src/new_etf_insight/daily_pipeline.py`

## 필수 환경

- 작업 디렉터리는 `etl`이다.
- `.env`에 `DART_API_KEY`가 있어야 한다.
- Python 의존성은 `uv`로 실행한다.
- 생성 결과는 `etl/runs/...` 아래에 둔다. 이 경로는 Git 관리 대상이 아니다.

## LLM Provider 선택

기본 provider는 `codex`다.

OpenClaw의 LLM 런타임을 쓰려면 아래 환경변수를 설정한다.

```bash
export ETF_LLM_PROVIDER=openclaw
export OPENCLAW_LLM_COMMAND='openclaw llm --schema {output_schema_path} --out {output_path} --search {search}'
```

`OPENCLAW_LLM_COMMAND`는 서버의 실제 OpenClaw 명령에 맞게 조정한다.

명령 템플릿에는 아래 placeholder를 사용할 수 있다.

- `{output_schema_path}`: JSON Schema 파일 경로
- `{output_path}`: LLM 최종 JSON 응답을 저장해야 하는 파일 경로
- `{search}`: `true` 또는 `false`

OpenClaw 명령은 프롬프트를 `stdin`으로 받아야 한다.
OpenClaw 명령은 최종 JSON만 `{output_path}`에 UTF-8 텍스트로 써야 한다.

## 실행 예시

하루치 실행:

```bash
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; result = run_daily_pipeline('20260429', '20260429', Path('runs/20260429/records'), Path('runs/20260429/pdfs')); print(result)"
```

기간 실행:

```bash
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; result = run_daily_pipeline('20260401', '20260430', Path('runs/20260401-20260430/records'), Path('runs/20260401-20260430/pdfs')); print(result)"
```

검증용 단일 후보 실행:

```bash
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; result = run_daily_pipeline('20260429', '20260429', Path('runs/smoke/records'), Path('runs/smoke/pdfs'), max_pages=2, query='KB RISE 현대차고정피지컬AI'); print(result)"
```

## 성공 기준

실행 결과는 Python dict로 출력된다.

성공 예:

```python
{
  "begin": "20260429",
  "end": "20260429",
  "candidate_count": 1,
  "results": [
    {
      "rcept_no": "20260429000010",
      "etf_key": "00104500_ET942",
      "action": "created",
      "reason": "new_record"
    }
  ]
}
```

결과 JSON은 `records/{etf_key}.json`에 저장된다.

## 기재정정 처리 원칙

- `[기재정정]` 공시는 먼저 기존 `records/{etf_key}.json` 존재 여부를 본다.
- 기존 record가 있으면 PDF를 다시 분석하지 않는다.
- DART viewer 텍스트를 읽고 LLM으로 업데이트 필요 여부를 판단한다.
- `needs_update=false`면 기존 record를 수정하지 않는다.
- `needs_update=true`면 기존 record를 정정 내용 기준으로 갱신한다.
- `first_rcept_dt`는 유지한다.
- `revision_count`는 새 `rcept_no`일 때만 증가한다.

## 금지사항

- `downloads/`, `etl/runs/`, `__pycache__/`, `.venv/`를 Git에 추가하지 않는다.
- LLM이 JSON 외 텍스트를 `{output_path}`에 쓰면 안 된다.
- OpenClaw provider 사용 시 `OPENCLAW_LLM_COMMAND` 없이 실행하지 않는다.
- 기재정정이라는 이유만으로 PDF를 무조건 재다운로드하거나 재분석하지 않는다.

## 빠른 점검

코드 변경 후에는 최소한 아래를 실행한다.

```bash
uv run python -m unittest tests/test_pipeline_modules.py
```

현재 codex provider 기준 스모크 테스트는 아래 명령으로 확인된 적이 있다.

```bash
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; result = run_daily_pipeline('20260429', '20260429', Path('runs/adapter-smoke/records'), Path('runs/adapter-smoke/pdfs'), max_pages=2, query='KB RISE 현대차고정피지컬AI'); print(result)"
```
