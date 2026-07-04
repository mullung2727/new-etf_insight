import { test, expect } from "@playwright/test";

// /research 선택 다운로드 e2e — api(:8000)를 route mock. 실서버 불필요.
// apiBase() = http://<hostname>:8000 → localhost:8000 로 요청.
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

test("선택한 리포트만 다운로드 요청에 담긴다", async ({ page }) => {
  let postedIds: string[] | undefined;

  await page.route(`${API}/research/search*`, (r) =>
    r.fulfill({ json: [{ code: "005930", name: "삼성전자" }] })
  );
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

  await page.goto("/research");

  // 종목 검색 → 후보 선택
  await page.getByPlaceholder("예: 삼성전자 또는 005930").fill("삼성");
  await page.getByText("삼성전자", { exact: false }).click();

  // 조회
  await page.getByRole("button", { name: "조회" }).click();

  // 기본 선택: 미다운로드 2건(11,22) 체크, 다운로드됨(33) 미체크
  const cb11 = page.getByRole("checkbox", { name: "대신증권 2026-07-03 선택" });
  const cb22 = page.getByRole("checkbox", { name: "교보증권 2026-07-02 선택" });
  const cb33 = page.getByRole("checkbox", { name: "iM증권 2026-07-01 선택" });
  await expect(cb11).toBeChecked();
  await expect(cb22).toBeChecked();
  await expect(cb33).not.toBeChecked();

  // 11 해제 → 22 만 남김
  await cb11.click();
  await expect(cb11).not.toBeChecked();

  // 다운로드
  await page.getByRole("button", { name: /다운로드/ }).click();

  await expect
    .poll(() => postedIds)
    .toEqual(["22"]);
});

test("전체선택/해제/재선택/개별선택 토글", async ({ page }) => {
  await page.route(`${API}/research/search*`, (r) =>
    r.fulfill({ json: [{ code: "005930", name: "삼성전자" }] })
  );
  await page.route(`${API}/research/stock/005930/reports*`, (r) =>
    r.fulfill({
      json: { code: "005930", name: "삼성전자", total: REPORTS.length,
        already: REPORTS.filter((x) => x.downloaded).length, reports: REPORTS },
    })
  );
  await page.goto("/research");
  await page.getByPlaceholder("예: 삼성전자 또는 005930").fill("삼성");
  await page.getByText("삼성전자", { exact: false }).click();
  await page.getByRole("button", { name: "조회" }).click();

  const header = page.getByRole("checkbox", { name: "전체 선택" });
  const c11 = page.getByRole("checkbox", { name: "대신증권 2026-07-03 선택" });
  const c22 = page.getByRole("checkbox", { name: "교보증권 2026-07-02 선택" });
  const c33 = page.getByRole("checkbox", { name: "iM증권 2026-07-01 선택" });

  await header.click(); // 전체선택
  await expect(c11).toBeChecked();
  await expect(c33).toBeChecked();
  await header.click(); // 전체해제
  await expect(c11).not.toBeChecked();
  await expect(c22).not.toBeChecked();
  await header.click(); // 재선택
  await expect(c22).toBeChecked();
  await header.click(); // 다시 해제
  await c22.click(); // 개별선택
  await expect(c22).toBeChecked();
  await expect(c11).not.toBeChecked();
  await expect(page.getByText("1건 선택됨")).toBeVisible();
});

test("선택 0건이면 다운로드 버튼 비활성", async ({ page }) => {
  await page.route(`${API}/research/search*`, (r) =>
    r.fulfill({ json: [{ code: "005930", name: "삼성전자" }] })
  );
  await page.route(`${API}/research/stock/005930/reports*`, (r) =>
    r.fulfill({
      json: {
        code: "005930", name: "삼성전자", total: 1, already: 1,
        reports: [{ researchId: "33", brokerName: "iM증권", title: "t", writeDate: "2026-07-01", downloaded: true, pdfKey: "33" }],
      },
    })
  );

  await page.goto("/research");
  await page.getByPlaceholder("예: 삼성전자 또는 005930").fill("삼성");
  await page.getByText("삼성전자", { exact: false }).click();
  await page.getByRole("button", { name: "조회" }).click();

  // 기본 선택 없음(유일 항목이 이미 받음) → 다운로드 버튼 disabled
  await expect(page.getByRole("button", { name: /다운로드/ })).toBeDisabled();
});

test("받은 리포트만 보기 링크가 뜨고 pdf 엔드포인트로 연결된다", async ({ page }) => {
  await page.route(`${API}/research/search*`, (r) =>
    r.fulfill({ json: [{ code: "005930", name: "삼성전자" }] })
  );
  await page.route(`${API}/research/stock/005930/reports*`, (r) =>
    r.fulfill({
      json: { code: "005930", name: "삼성전자", total: REPORTS.length,
        already: REPORTS.filter((x) => x.downloaded).length, reports: REPORTS },
    })
  );
  await page.goto("/research");
  await page.getByPlaceholder("예: 삼성전자 또는 005930").fill("삼성");
  await page.getByText("삼성전자", { exact: false }).click();
  await page.getByRole("button", { name: "조회" }).click();

  // 받은 것(33)만 보기 링크, 안 받은 것(11,22)엔 없음
  const viewLinks = page.getByRole("link", { name: "보기" });
  await expect(viewLinks).toHaveCount(1);
  const href = await viewLinks.getAttribute("href");
  expect(href).toContain(`${API}/research/stock/005930/reports/33/pdf?`);
  expect(href).toContain("writeDate=2026-07-01");
  expect(href).toContain("brokerName=iM%EC%A6%9D%EA%B6%8C");
  expect(href).toContain("pdfKey=33");
});
