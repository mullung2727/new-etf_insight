---
name: new-etf-insight-plan-writing
description: Checklist for writing or updating a PLAN/design doc before implementation in this repo. Read this before authoring a docs PLAN so decisions are concrete, enforcement paths are verified, and requirements map 1:1 to tests. Prevents "auto-guaranteed" style false assumptions.
---

# PLAN(설계문서) 작성 지침

PLAN을 새로 쓰거나 고칠 때 **먼저 이 체크리스트를 통과**시킨다.
목적: 애매한 단정으로 요구사항이 조용히 누락되는 사고 방지.

## 1. 단정 금지 — "자동 보장"은 grep으로 증명

"자동 보장 / 이미 처리됨 / 원칙상 불가능 / ~로 자연히 제외" 같은 문장을 쓰기 전:

- 그 보장을 **실제로 강제하는 코드(함수)** 를 grep한다.
- 그 함수가 **어느 호출 경로에서 도는지** 전부 나열한다.
- **신규 진입점**(수동 create, 새 API, 새 CLI, 새 UI 버튼)이 그 강제 경로를 **우회**하는지 확인.
- 강제 지점이 1곳이면, 그 1곳을 안 거치는 경로가 곧 구멍이다.

> 실제 사고: "종목당 노트 1개 → merge_notes_by_symbol이 자동 보장"이라 단정.
> merge는 체결 자동연결에서만 돌고 수동 create_note는 우회 → 중복 노트 무한 생성.
> 참고 메모리: verify-invariant-enforcement-path.

## 2. 애매어 금지 — 구체 조건/경로/함수명으로

- ✕ "적절히 / 자동으로 / 알아서 / 필요 시" → ○ 구체 조건, 함수명, 상태값, 임계치.
- 각 "확정 결정"은 **어느 파일 어느 함수가 그걸 구현하는지** 한 줄로 지목.

## 3. 요구사항 → 테스트 1:1 매핑

- 사용자가 말한 요구사항 하나하나에 **검증 가능한 테스트 케이스**를 매핑.
- 매핑 안 되는 요구사항 = 빠진 것. 특히 "~하면 안 된다"류 부정 요구는 **거부/차단 테스트**로 남긴다.
- 중복 금지·유니크 같은 불변식은 "우회 경로에서 시도 → 거부" 테스트를 반드시 포함.

## 4. "손 안 대는 것" 항목은 요구사항 커버 재확인

- PLAN에 "수정 없음 / 손 안 댐"으로 적은 코드가 **실제로 요구사항을 다 커버하는지** 다시 본다.
- 누락은 보통 여기 숨는다("이건 기존 걸로 되니 안 건드림"이 사실은 우회 경로일 때).

## 5. 구현 완료 후 재검토(반드시)

- 구현 끝나고 **PLAN을 다시 열어** 각 확정 결정·범위 항목을 코드와 대조.
- "완료" 보고 전에: 요구사항 목록 ↔ 구현 ↔ 테스트 세 개가 전부 맞물리는지 확인.
- PLAN 범위대로 다 했어도, PLAN의 가정 자체가 틀렸을 수 있음(1번). 보고 시 "PLAN 기준 완료"와
  "요구사항 기준 완료"를 구분해 생각한다.
