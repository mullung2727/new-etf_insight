import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

// 언급 통합 탭 e2e — 텔레그램+유튜브 언급을 한 타임라인. client fetch → route mock.
const API = "http://localhost:8000";

const TG = [
  {
    date_kst: "2026-07-03", session: "close", channels: ["tgA"],
    post_refs: ["c/1/2"], change_type: "new", change_summary: "텔레그램 언급 요약", themes: ["반도체"],
  },
];
const YT = [
  {
    date_kst: "2026-07-05", name: "삼성전자", channels: ["ytChan"],
    video_ids: ["vid1"], discovery_reason: "유튜브 발굴 이유", analysis: "유튜브 언급 분석",
  },
];

async function mockPeers(page: Page) {
  await page.route(`${API}/telegram/theme-peers/005930*`, (r) => r.fulfill({ json: [] }));
}

test("텔레그램+유튜브 언급이 날짜 desc 한 목록에 소스뱃지로 섞여 표시", async ({ page }) => {
  await page.route(`${API}/telegram/mentions/005930*`, (r) => r.fulfill({ json: TG }));
  await page.route(`${API}/youtube/mentions/005930*`, (r) => r.fulfill({ json: YT }));
  await mockPeers(page);

  await page.goto("/stock/005930?tab=mentions&name=삼성전자");

  await expect(page.getByText("텔레그램 언급 요약")).toBeVisible();
  await expect(page.getByText("유튜브 언급 분석")).toBeVisible();

  // 날짜 desc: 유튜브(07-05)가 텔레그램(07-03)보다 앞
  const rows = page.getByTestId("mention-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("유튜브 언급 분석");
  await expect(rows.nth(1)).toContainText("텔레그램 언급 요약");
});

test("소스 필터 = 유튜브 → 텔레그램 행 숨김", async ({ page }) => {
  await page.route(`${API}/telegram/mentions/005930*`, (r) => r.fulfill({ json: TG }));
  await page.route(`${API}/youtube/mentions/005930*`, (r) => r.fulfill({ json: YT }));
  await mockPeers(page);

  await page.goto("/stock/005930?tab=mentions&name=삼성전자");
  await expect(page.getByTestId("mention-row")).toHaveCount(2);

  await page.getByTestId("mention-filter-yt").click();
  await expect(page.getByText("유튜브 언급 분석")).toBeVisible();
  await expect(page.getByText("텔레그램 언급 요약")).toHaveCount(0);
});

test("한쪽(텔레그램) 실패해도 나머지(유튜브) 표시", async ({ page }) => {
  await page.route(`${API}/telegram/mentions/005930*`, (r) => r.abort());
  await page.route(`${API}/youtube/mentions/005930*`, (r) => r.fulfill({ json: YT }));
  await mockPeers(page);

  await page.goto("/stock/005930?tab=mentions&name=삼성전자");
  await expect(page.getByText("유튜브 언급 분석")).toBeVisible();
});
