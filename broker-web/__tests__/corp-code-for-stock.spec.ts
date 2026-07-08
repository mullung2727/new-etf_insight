import { test, expect } from "@playwright/test";
import { corpCodeForStock } from "@/lib/dart";
import type { CorpCode } from "@/types/dart";

const corps: CorpCode[] = [
  { corp_code: "00126380", corp_name: "삼성전자", stock_code: "005930" },
  { corp_code: "00164779", corp_name: "SK하이닉스", stock_code: "000660" },
  { corp_code: "00999999", corp_name: "비상장기업", stock_code: "" },
];

test("known stock_code maps to corp_code", () => {
  expect(corpCodeForStock(corps, "005930")).toBe("00126380");
  expect(corpCodeForStock(corps, "000660")).toBe("00164779");
});

test("unknown stock_code → null", () => {
  expect(corpCodeForStock(corps, "999999")).toBeNull();
});

test("empty input never matches blank stock_code rows", () => {
  expect(corpCodeForStock(corps, "")).toBeNull();
  expect(corpCodeForStock(corps, "   ")).toBeNull();
});

test("trims surrounding whitespace", () => {
  expect(corpCodeForStock(corps, " 005930 ")).toBe("00126380");
});
