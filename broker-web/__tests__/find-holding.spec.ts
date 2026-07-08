import { test, expect } from "@playwright/test";
import { findHolding } from "@/lib/stock-hub";
import type { Holding } from "@/lib/broker-client";

const holdings: Holding[] = [
  { stk_cd: "A005930", stk_nm: "삼성전자", evltv_prft: "12000", prft_rt: "3.2" },
  { stk_cd: "000660", stk_nm: "SK하이닉스", evltv_prft: "-5000", prft_rt: "-1.1" },
];

test("matches despite kt00018 'A' prefix on stk_cd", () => {
  expect(findHolding(holdings, "005930")?.evltv_prft).toBe("12000");
});

test("matches plain 6-digit stk_cd", () => {
  expect(findHolding(holdings, "000660")?.prft_rt).toBe("-1.1");
});

test("not held → null", () => {
  expect(findHolding(holdings, "999999")).toBeNull();
});

test("undefined holdings → null (not holding / balance failed)", () => {
  expect(findHolding(undefined, "005930")).toBeNull();
});
