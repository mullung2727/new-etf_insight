# Project Instructions

## Skills

- When running or debugging the ETF batch pipeline, first read `skills/new-etf-insight-batch/SKILL.md`.
- When reading, modifying, or extending ETL pipeline code (modules, functions, data flow, schemas), first read `skills/new-etf-insight-etl-reference/SKILL.md`.
- When starting, restarting, checking, or debugging local project servers, first read `skills/new-etf-insight-server-dev/SKILL.md`.
- When writing or updating a PLAN/design doc (설계문서) before implementation, first read `skills/new-etf-insight-plan-writing/SKILL.md`.
- When backtesting a trading strategy (일봉/분봉 데이터 조회, 청산 시뮬레이션, 표본 확장), first read `research/BACKTEST_DATA.md`.
- When committing, branching, opening a PR, or handling CodeRabbit review comments, first read `skills/new-etf-insight-git-workflow/SKILL.md`.

## 코드 탐색

- 코드 구조 파악·정의 찾기·참조 추적은 grep 대신 serena MCP를 먼저 쓴다.
  - `mcp__serena__get_symbols_overview` — 파일 안 열고 클래스/함수 목록만
  - `mcp__serena__find_symbol` — 정의 위치 + 메서드 트리
  - `mcp__serena__find_referencing_symbols` — 누가 호출하는지 크로스파일 추적
- serena 툴은 deferred라 세션 첫 사용 시 `ToolSearch`로 한 번에 로드할 것.
- grep은 문자열 검색(로그 메시지, 설정값, 주석)에만 쓴다.

이 문서는 `new-etf-insight` 프로젝트에서 에이전트가 항상 따라야 하는 최상위 작업 지침이다.
코드 수정, 설계, 테스트, 응답 방식은 반드시 이 문서를 우선한다.

## 기본 응답 규칙

- 항상 반말, 
- 항상 개조식으로 대답할 것
- 항상 한글로 대답할 것

## 질문 답변 규칙

- 사용자가 질문하면 먼저 질문의 핵심에만 답한다.
- 사용자의 핵심 질문이 모호하면 질문을 명확하게 하기 위한 질문을 먼저 한다.
- 사용자가 요청하지 않은 구현안, 장황한 배경 설명, 과한 대안 제시는 피한다.
- 사용자가 방법을 물으면, 먼저 현재 코드/문서/상태에서 이미 가능한 방법을 기준으로 답한다.
- 새 구현, 새 파일, 구조 변경, 대안 설계는 기존 방법으로 불가능하거나 사용자가 명시적으로 요청한 경우에만 제안한다.
- 답변은 가능한 한 짧게 한다.
- 추가 설명이 필요하면 "더 자세히 볼까?"처럼 확인한 뒤 이어간다.
- 사용자의 질문이 코드 수정 요청이 아니라면 파일을 수정하지 않는다.
- 사용자의 지적이 사실과 다르면 수긍하지 않는다. 먼저 "그건 N번째 답 어디에 있었다"처럼 근거를 대고 짚는다. 틀린 지적에 수긍하면 사용자가 잘못된 전제로 지침을 바꾸게 된다.
- 답이 이미 있었는데 못 봤다면 원인은 사용자가 아니라 답의 길이·배치다. 다시 설명하지 말고 위치를 알려준 뒤 짧게 줄여 다시 낸다.

## 응답 길이 규칙

- 답변은 기본적으로 짧게 한다.
- 사용자가 명시적으로 긴 설명, 상세 분석, 전체 계획을 요청하지 않으면 핵심만 답한다.
- 추가 설명이 필요하면 본문에 길게 쓰지 말고, 마지막에 2~3줄로 요약한다.
- 요약 끝에는 사용자가 원할 때만 이어서 설명할 수 있게 확인 질문을 붙인다.
- 한 턴에 사용자가 조치해야 할 사항이 여러 개면, 첫 번째 것만 제시하고 완료 확인 후 다음으로 넘어간다.
- 구현 완료 후 "다음 단계는 X입니다. 진행할까요?" 형식으로 멈추고 사용자 응답을 기다린다.

## 코드 수정 규칙

- 코드 수정 전 `README.md`(구성·포트·데이터 흐름·보안 경계)를 참고할 것
- 사용하는 라이브러리, 프레임워크, API의 버전에 주의하여 수정안을 제시할 것

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them. Don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### Critical Rule

절대 추정하지 말 것.

다음 상황에서는 작업을 중단하고 질문할 것:

- 요구사항이 2개 이상으로 해석 가능
- 파일명이 명시되지 않음
- API 선택이 명시되지 않음
- DB 스키마가 확정되지 않음

위 상황에서 추정 후 작업하면 실패로 간주한다.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- 코드 제안 전 반드시 “현재 구조 안에서 가장 작은 변경으로 해결 가능한가?”를 먼저 판단하고, 가능하면 그 방식만 제안한다.
- 사용자가 명시하지 않은 새 CLI 옵션, 새 진입점, 새 파일, 새 추상화, 새 저장 규칙은 기존 구조로 해결 불가능할 때만 제안한다.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it. Don't delete it.

When your changes create orphans:

- Remove imports, variables, and functions that your changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" -> "Write tests for invalid inputs, then make them pass"
- "Fix the bug" -> "Write a test that reproduces it, then make it pass"
- "Refactor X" -> "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```text
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Strong success criteria let Codex loop independently. Weak criteria such as "make it work" require clarification.

