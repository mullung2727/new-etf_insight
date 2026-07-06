import { test, expect } from "@playwright/test";
import { deriveQ4List } from "../lib/dart-quarterly";

// 최소 픽스처: IS 매출(기간) 1개 + BS 자산총계(시점) 1개
function resp(rev: string | null, assets: string) {
  return {
    status: "000",
    message: "",
    list: [
      { sj_div: "IS", sj_nm: "손익", account_nm: "매출액", account_id: "ifrs-full_Revenue",
        thstrm_amount: rev, frmtrm_amount: null, bfefrmtrm_amount: null, currency: "KRW", ord: "1" },
      { sj_div: "BS", sj_nm: "재무상태", account_nm: "자산총계", account_id: "ifrs-full_Assets",
        thstrm_amount: assets, frmtrm_amount: null, bfefrmtrm_amount: null, currency: "KRW", ord: "2" },
    ],
  };
}
const get = (list: ReturnType<typeof deriveQ4List>, id: string) =>
  list.find(i => i.account_id === id)!.thstrm_amount;

test("IS 기간항목: Q4 = FY − Q1 − Q2 − Q3", () => {
  // FY 매출 300, Q1 71 Q2 74 Q3 79 → Q4 = 76
  const out = deriveQ4List(resp("300", "1000"), resp("71", "0"), resp("74", "0"), resp("79", "0"));
  expect(get(out, "ifrs-full_Revenue")).toBe("76");
});

test("BS 시점항목: Q4 = FY 연말잔액 그대로 (차감 안 함)", () => {
  const out = deriveQ4List(resp("300", "1000"), resp("71", "111"), resp("74", "222"), resp("79", "333"));
  expect(get(out, "ifrs-full_Assets")).toBe("1000");
});

test("분기 하나라도 결측이면 기간항목 null", () => {
  const out = deriveQ4List(resp("300", "1000"), resp(null, "0"), resp("74", "0"), resp("79", "0"));
  expect(get(out, "ifrs-full_Revenue")).toBeNull();
  expect(get(out, "ifrs-full_Assets")).toBe("1000"); // 시점항목은 영향 없음
});

test("음수 결과도 정상 (적자전환 등)", () => {
  const out = deriveQ4List(resp("100", "0"), resp("40", "0"), resp("40", "0"), resp("50", "0"));
  expect(get(out, "ifrs-full_Revenue")).toBe("-30");
});
