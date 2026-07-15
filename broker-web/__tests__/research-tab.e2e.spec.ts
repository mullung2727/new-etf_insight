import { test, expect } from "@playwright/test";

// 리포트 탭 e2e — /stock/[code]?tab=research 로 진입.
// 검색 없이 진입 즉시 자동 조회, 목록·선택·다운로드는 기존 /research 로직 재사용.
const API = "http://localhost:8000";

type Report = {
  researchId: string;
  brokerName: string;
  title: string;
  writeDate: string;
  downloaded: boolean;
  pdfKey: string;
};

const REPORTS: Report[] = [
  { researchId: "11", brokerName: "대신증권", title: "실적 상향", writeDate: "2026-07-03", downloaded: false, pdfKey: "11" },
  { researchId: "22", brokerName: "교보증권", title: "목표가 유지", writeDate: "2026-07-02", downloaded: false, pdfKey: "22" },
  { researchId: "33", brokerName: "iM증권", title: "리스크 점검", writeDate: "2026-07-01", downloaded: true, pdfKey: "33" },
];

async function mockReports(page: import("@playwright/test").Page) {
  await page.route(`${API}/research/stock/005930/reports*`, (r) =>
    r.fulfill({
      json: {
        code: "005930",
        name: "삼성전자",
        total: REPORTS.length,
        already: REPORTS.filter((x) => x.downloaded).length,
        reports: REPORTS,
      },
    })
  );
}

test("진입 즉시 목록 자동조회 + 기본선택(미다운로드만)", async ({ page }) => {
  await mockReports(page);
  await page.goto("/stock/005930?tab=research&name=삼성전자");

  const cb11 = page.getByRole("checkbox", { name: "대신증권 2026-07-03 선택" });
  const cb22 = page.getByRole("checkbox", { name: "교보증권 2026-07-02 선택" });
  const cb33 = page.getByRole("checkbox", { name: "iM증권 2026-07-01 선택" });

  // 조회 버튼 클릭 없이 자동 표시
  await expect(cb11).toBeChecked();
  await expect(cb22).toBeChecked();
  await expect(cb33).not.toBeChecked();
});

test("선택한 리포트만 다운로드 요청에 담긴다", async ({ page }) => {
  let postedIds: string[] | undefined;
  await mockReports(page);
  await page.route(`${API}/research/stock/005930/download`, (r) => {
    postedIds = r.request().postDataJSON().researchIds;
    r.fulfill({
      json: {
        job_id: "job1", status: "running", code: "005930", name: "삼성전자",
        total: postedIds?.length ?? 0, downloaded: 0, skipped: 0, failed: 0, error: null,
      },
    });
  });
  await page.route(`${API}/research/jobs/job1`, (r) =>
    r.fulfill({
      json: {
        job_id: "job1", status: "done", code: "005930", name: "삼성전자",
        total: postedIds?.length ?? 0, downloaded: postedIds?.length ?? 0,
        skipped: 0, failed: 0, error: null,
      },
    })
  );

  await page.goto("/stock/005930?tab=research&name=삼성전자");

  const cb11 = page.getByRole("checkbox", { name: "대신증권 2026-07-03 선택" });
  await expect(cb11).toBeChecked();
  await cb11.click(); // 11 해제 → 22만 남김
  await expect(cb11).not.toBeChecked();

  await page.getByRole("button", { name: /다운로드/ }).click();
  await expect.poll(() => postedIds).toEqual(["22"]);
});
