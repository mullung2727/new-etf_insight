import { test, expect } from "@playwright/test";
import { rimBasic, rimDetail, avgGrowth } from "@/components/financial/RimPanel";

const EOK = 1e8;

test("rimBasic: ROE>r → 프리미엄 (손계산 일치)", () => {
  // 100억, ROE 10%, r 8%, w 0.9 → excess 2억, 2*0.9/0.18=10억 → 110억
  expect(rimBasic(100 * EOK, 10, 8, 0.9)).toBeCloseTo(110 * EOK, 0);
});

test("rimBasic: ROE=r → 초과이익 0, 적정가치=자기자본", () => {
  expect(rimBasic(100 * EOK, 8, 8, 0.9)).toBeCloseTo(100 * EOK, 0);
});

test("rimBasic: 발산(w≥1+r) → null", () => {
  expect(rimBasic(100 * EOK, 10, 8, 1.1)).toBeNull();
});

test("rimDetail: 순이익=r×B0 지속 → RI 0, 적정가치≈자기자본", () => {
  // NI = r*equity 매년, 배당성향 100%(유보 0)면 B 불변 → RI 매년 0
  const eq = 100 * EOK;
  const ni = Array(5).fill(8 * EOK); // r=8% of 100억
  expect(rimDetail(eq, ni, 8, 0.9, 100)).toBeCloseTo(eq, 0);
});

test("rimDetail: 잘못된 입력(길이≠5) → null", () => {
  expect(rimDetail(100 * EOK, [1, 2, 3], 8, 0.9, 20)).toBeNull();
});

test("avgGrowth: 매년 +10% → 0.1", () => {
  expect(avgGrowth([100, 110, 121])).toBeCloseTo(0.1, 6);
});

test("avgGrowth: 적자(직전값≤0) 구간 배제", () => {
  // -50→100 은 분모 음수라 스킵, 100→150(+0.5)만 반영
  expect(avgGrowth([-50, 100, 150])).toBeCloseTo(0.5, 6);
});

test("avgGrowth: 데이터 부족/전부 적자 → 0", () => {
  expect(avgGrowth([])).toBe(0);
  expect(avgGrowth([null, 100])).toBe(0);
});
