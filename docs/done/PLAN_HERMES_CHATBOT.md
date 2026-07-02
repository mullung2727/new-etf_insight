# Hermes 대화형 투자 챗봇 (완료)

## 한 줄 요약
대화형 투자 어시스턴트의 두뇌 = **Nous Research Hermes Agent**(셀프호스트), 프론트 = **텔레그램 봇 단일**. 자작 웹 챗 없이 기존 kiwoom-broker·etf-insight MCP를 Hermes에 붙여 대화로 조회·주문·분석·노트 수행. broker-web은 대시보드 전용 유지.

---

## 최종 아키텍처

```
[Hermes Agent — 유저 PC, 아웃바운드 연결만]
   ├─ 텔레그램 봇          ← 유일한 채팅 프론트
   ├─ kiwoom-broker MCP    ← broker:8001/mcp (시세·잔고·주문·노트, 27 tools)
   ├─ etf-insight MCP      ← api:8000/mcp (ETF·watchlist·KRX 일봉, 9 tools)
   └─ SOUL.md              ← 도구 사용 규칙 (docs/hermes/SOUL.md가 원본, ~/.hermes/로 복사)
```

---

## 완료된 것

| 항목 | 내용 |
|------|------|
| 1. Hermes 설치+모델 | 설치·모델 설정·`hermes doctor` 정상 |
| 2. kiwoom MCP 연결 | config.yaml에 `http://localhost:8001/mcp`, 도구 27개 등록 |
| 3. 텔레그램 게이트웨이 | 봇 연결, 조회·주문 실증(place_order 0.63초 체결) |
| 5. ETF·watchlist 조회 | api가 이미 MCP 서빙 중이라 래퍼 불요 — config 한 줄로 연결 |
| SOUL.md 도구 지침 | 도구 직접호출 강제·우회 세션 금지·주문 confirm 절차. 원본 `docs/hermes/SOUL.md` |
| broker 도구 사용성 | place_order docstring 사용예 명시, health_check MCP 노출 |

### 운영 시 주의 (실측 교훈)

- **도구 목록은 게이트웨이가 연결 시점에 스냅샷** — broker/api 재시작하면 게이트웨이도
  재시작(또는 `/reload-mcp`). 어제 게이트웨이가 broker보다 먼저 떠서 `registered 0 tool(s)`
  로 시작한 실사례 있음. 부팅 순서: broker·api 먼저 → 게이트웨이 나중.
- **봇이 느렸던 실제 원인**(2026-07-02 삽질 부검): 도구는 등록돼 있었는데 봇이
  `--toolsets`로 MCP를 뺀 우회 세션을 띄우고 엉뚱한 도구를 탐색. 서버·docstring 문제
  아니었음. SOUL.md 규칙(우회 금지·무관도구 금지·안 보이면 보고 후 정지)으로 차단.

---

## 스킵/제외 (사유)

| 항목 | 처리 | 사유 |
|------|------|------|
| 보안(allowlist·2FA·inline confirm) | 스킵 | 개인 사설 봇 + 봇토큰 관리로 충분 판단. 토큰 유출 시 2차 방어선 필요해지면 `TELEGRAM_ALLOWED_USERS` 또는 `hermes pairing` 도입 |
| 4. 웹검색 | 안 함 | Hermes 설정 토글 수준 — 필요할 때 켜면 됨 |
| 6. 리포트/PDF 분석 MCP 래퍼 | 제외(YAGNI) | dart_pdf 파이프라인은 ETL 배치용. 채팅 PDF 분석은 Hermes 자체 도구로 충분. DB 적재까지 시키고 싶어지면 그때 |
| 7. 배포 패키징 | 안 함 | 타인 배포 시점에. SOUL.md 템플릿이 첫 재료 |

관련 메모리: [[project-hermes-chatbot-architecture]]
