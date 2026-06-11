# 디자인 시스템 도입 계획

검토 기준일: 2026-06-10  
대상: broker-web 전체 프론트엔드

## 현황 문제

| 문제 | 위치 | 영향 |
|---|---|---|
| 동일 색상 3곳 분산 | `globals.css` + `CandleChart.tsx` + `FinancialChart.tsx` (C_PRIMARY="#F0B429") | 수정 시 3곳 동기화 필요 |
| 타이포 스케일 없음 | fin-scope 전체: `text-[9px]` `text-[10px]` `text-[11px]` `text-[13px]` 혼재 | 일관성 없음, 리뷰 어려움 |
| 불투명도 계층 비명시 | `text-white/10~80`, `text-primary/30~80` 암묵적 의미로 사용 | "몇 %가 어떤 역할인지" 코드에서 불분명 |
| 상태 색상 미토큰화 | `emerald-400`, `red-400`, `blue-400`, `yellow-400` 컴포넌트 직접 사용 | 상태별 의미가 어디서든 다를 수 있음 |
| 두 테마 토큰 혼재 | shadcn 토큰(blue-tinted primary) + fin-scope(gold primary) 동일 `--primary` 덮어씀 | .fin-scope 밖에서 fin 컴포넌트 쓰면 색 깨짐 |

## 설계 방향

1. **CSS vars = 단일 진실** — JS 하드코딩 제거, `globals.css`에서만 정의
2. **fin-scope 토큰 확장** — `.fin-scope` 안에서 쓰는 토큰 전부 명시 (현재 3개뿐)
3. **타이포 스케일** — fin-scope용 `text-fin-xs/sm/base/lg` (9/10/11/13px) Tailwind 확장
4. **시맨틱 토큰** — 역할(레이블/값/보조값/비활성) 기반 네이밍, 숫자 불투명도 직접 노출 않음
5. **차트 토큰** — CSS var에서 JS로 읽어오는 단일 export (`lib/tokens.ts`)

## 구현 단계

### Step 1 — 토큰 정의 확장 (globals.css)

**fin-scope 추가 토큰:**
```css
.fin-scope {
  /* 기존 */
  --primary: oklch(0.78 0.165 77);    /* #F0B429 골드 */
  --up:      oklch(0.627 0.258 29);   /* 손실/위험 빨강 */

  /* 신규 */
  --fin-text-primary:   rgba(255,255,255,0.80);  /* 값 (당기) */
  --fin-text-secondary: rgba(255,255,255,0.55);  /* 레이블, 보조 */
  --fin-text-muted:     rgba(255,255,255,0.30);  /* 비활성 */
  --fin-text-ghost:     rgba(255,255,255,0.15);  /* null/— */
  --fin-text-ratio:     oklch(0.78 0.165 77 / 0.55); /* 인라인 비율 (primary/55) */
  --fin-accent-gold:    oklch(0.78 0.165 77 / 0.70); /* 강조 레이블 */
  --fin-bg-row-alt:     rgba(0,0,0,0.08);
  --fin-bg-row-total:   oklch(0.78 0.165 77 / 0.04);
  --fin-bg-section-hdr: rgba(0,0,0,0.25);
  --fin-border-primary: oklch(0.78 0.165 77 / 0.15);
  --fin-border-subtle:  rgba(255,255,255,0.04);

  /* 상태 색상 (notes, 브로커 상태) */
  --status-open:    oklch(0.70 0.18 145);  /* emerald */
  --status-partial: oklch(0.75 0.16 77);   /* amber */
  --status-closed:  rgba(255,255,255,0.40);
  --status-profit:  oklch(0.70 0.18 145);  /* = profit */
  --status-loss:    oklch(0.627 0.258 29); /* = up */
}
```

**Tailwind @theme 등록:**
```css
@theme inline {
  --color-fin-primary:   var(--fin-text-primary);
  --color-fin-secondary: var(--fin-text-secondary);
  --color-fin-muted:     var(--fin-text-muted);
  --color-fin-ghost:     var(--fin-text-ghost);
  --color-fin-ratio:     var(--fin-text-ratio);
  --color-status-open:   var(--status-open);
  --color-status-partial:var(--status-partial);
  --color-status-closed: var(--status-closed);
  --color-status-profit: var(--status-profit);
  --color-status-loss:   var(--status-loss);

  /* fin-scope 타이포 스케일 */
  --text-fin-xs:   9px;
  --text-fin-sm:   10px;
  --text-fin-base: 11px;
  --text-fin-lg:   13px;
}
```

**결과:** `text-white/80` → `text-fin-primary`, `text-primary/55` → `text-fin-ratio` 등으로 교체

---

### Step 2 — JS 토큰 상수 (`lib/tokens.ts`)

```ts
// CSS var에서 읽어오는 단일 export — CandleChart, FinancialChart 공용
export const CHART_TOKENS = {
  primary: "var(--color-primary)",   // #F0B429
  up:      "var(--color-up)",        // #ef4444
  down:    "#3b82f6",                // 차트 전용 (fin-scope에 --down 추가)
  green:   "var(--color-status-profit)",
} as const;
```

제거 대상: `CandleChart.tsx`의 `C_PRIMARY = "#F0B429"` 등 하드코딩 3곳

---

### Step 3 — 컴포넌트 토큰 마이그레이션

| 컴포넌트 | 교체 대상 | 신규 토큰 |
|---|---|---|
| `CompareTable.tsx` | `text-white/80`, `text-white/55`, `text-white/15`, `text-primary/80`, `text-primary/55`, `text-primary/60`, `text-primary/70`, `bg-black/08`, `bg-black/25`, `bg-primary/[0.04]`, `border-primary/[0.15]`, `border-white/[0.04]` | `text-fin-*`, `bg-fin-*`, `border-fin-*` Tailwind 유틸리티 |
| `CandleChart.tsx` | `C_PRIMARY`, `C_UP`, `C_DOWN` 하드코딩 | `CHART_TOKENS` import |
| `FinancialChart.tsx` | 동일 | 동일 |
| `note-card.tsx` | `text-emerald-400`, `text-blue-400`, `text-yellow-400` | `text-status-open`, `text-status-partial` |
| `change-badge.tsx` | `text-emerald-400`, `text-red-400` | `text-status-profit`, `text-status-loss` |
| `broker-status.tsx` | `bg-emerald-400`, `bg-red-500` | `bg-status-open`, `bg-status-loss` |

---

### Step 4 — 타이포 스케일 적용 (fin-scope 컴포넌트)

| 현재 | 교체 |
|---|---|
| `text-[9px]` | `text-fin-xs` |
| `text-[10px]` | `text-fin-sm` |
| `text-[11px]` | `text-fin-base` |
| `text-[13px]` | `text-fin-lg` |

대상 파일: `CompareTable.tsx`, `CompareSearchBar.tsx`, `FinancialChart.tsx`, `FinancialTable.tsx`, `SearchBar.tsx`

---

## 작업 우선순위

| 단계 | 범위 | 리스크 | 예상 크기 |
|---|---|---|---|
| Step 1 | globals.css만 | 낮음 | S |
| Step 2 | lib/tokens.ts 신규 | 낮음 | XS |
| Step 3 — 차트 | CandleChart + FinancialChart | 낮음 | S |
| Step 3 — 상태 색상 | note-card, change-badge, broker-status | 낮음 | S |
| Step 3 — CompareTable | 가장 복잡한 컴포넌트, fin 토큰 집약 | 중간 | M |
| Step 4 | 타이포 스케일 (find-replace 수준) | 낮음 | S |

## 확정 결정 (2026-06-10)

- [x] `--down` → globals.css `:root`에 추가 (`#3b82f6`)
- [x] 범위: 사이트 전체 (ETF/trading/notes 포함)
- [x] B안 채택: `--fin-gold` 별도 토큰, `--primary` 더 이상 덮어쓰지 않음
  - `.fin-scope`의 `--primary: gold` 오버라이드 제거
  - fin 컴포넌트 내 `text-primary/*` → `text-fin-gold/*` 전면 교체
  - CompareTable 등이 `.fin-scope` 없이 쓰여도 색상 안 깨짐
