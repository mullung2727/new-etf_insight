import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// GET /api/etfs
// ---------------------------------------------------------------------------

test("GET /api/etfs returns etfs array and countries array", async ({ request }) => {
  const res = await request.get("/api/etfs");
  expect(res.status()).toBe(200);

  const body = await res.json();
  expect(body).toHaveProperty("etfs");
  expect(body).toHaveProperty("countries");
  expect(Array.isArray(body.etfs)).toBe(true);
  expect(Array.isArray(body.countries)).toBe(true);
  expect(body.etfs.length).toBeGreaterThan(0);
});

test("GET /api/etfs etf item has required fields", async ({ request }) => {
  const res = await request.get("/api/etfs");
  const { etfs } = await res.json();
  const item = etfs[0];

  expect(item).toHaveProperty("etf_key");
  expect(item).toHaveProperty("fund_name");
  expect(item).toHaveProperty("asset_manager");
  expect(item).toHaveProperty("first_rcept_dt");
  expect(item).toHaveProperty("is_pre_listing_etf");
});

test("GET /api/etfs?pre_listing=true returns only pre-listing ETFs", async ({ request }) => {
  const res = await request.get("/api/etfs?pre_listing=true");
  expect(res.status()).toBe(200);

  const { etfs } = await res.json();
  expect(etfs.length).toBeGreaterThan(0);
  for (const etf of etfs) {
    expect(etf.is_pre_listing_etf).toBe(true);
  }
});

test("GET /api/etfs?begin=&end= date filter narrows results", async ({ request }) => {
  const all = await (await request.get("/api/etfs")).json();
  const filtered = await (
    await request.get("/api/etfs?begin=20260501&end=20260518")
  ).json();

  expect(filtered.etfs.length).toBeLessThanOrEqual(all.etfs.length);
  for (const etf of filtered.etfs) {
    expect(etf.first_rcept_dt >= "20260501").toBe(true);
    expect(etf.first_rcept_dt <= "20260518").toBe(true);
  }
});

// ---------------------------------------------------------------------------
// GET /api/etfs/[etf_key]
// ---------------------------------------------------------------------------

test("GET /api/etfs/[etf_key] returns detail and holdings", async ({ request }) => {
  // grab first etf_key from list
  const { etfs } = await (await request.get("/api/etfs")).json();
  const etfKey = etfs[0].etf_key;

  const res = await request.get(`/api/etfs/${etfKey}`);
  expect(res.status()).toBe(200);

  const body = await res.json();
  expect(body).toHaveProperty("detail");
  expect(body).toHaveProperty("holdings");
  expect(body.detail.etf_key).toBe(etfKey);
  expect(Array.isArray(body.holdings)).toBe(true);
});

test("GET /api/etfs/[etf_key] detail has summary fields", async ({ request }) => {
  const { etfs } = await (await request.get("/api/etfs")).json();
  const { detail } = await (
    await request.get(`/api/etfs/${etfs[0].etf_key}`)
  ).json();

  expect(detail).toHaveProperty("fund_name");
  expect(detail).toHaveProperty("asset_manager");
  expect(detail).toHaveProperty("index_name");
  expect(detail).toHaveProperty("trend_summary");
  expect(detail).toHaveProperty("keywords");
  expect(Array.isArray(detail.keywords)).toBe(true);
});

test("GET /api/etfs/nonexistent returns 404", async ({ request }) => {
  const res = await request.get("/api/etfs/INVALID_KEY_XYZ");
  expect(res.status()).toBe(404);
  const body = await res.json();
  expect(body).toHaveProperty("error");
});

// ---------------------------------------------------------------------------
// GET /api/stats/holdings
// ---------------------------------------------------------------------------

test("GET /api/stats/holdings returns stats, summary and countries", async ({ request }) => {
  const res = await request.get("/api/stats/holdings");
  expect(res.status()).toBe(200);

  const body = await res.json();
  expect(body).toHaveProperty("stats");
  expect(body).toHaveProperty("summary");
  expect(body).toHaveProperty("countries");
  expect(Array.isArray(body.stats)).toBe(true);
  expect(typeof body.summary.total_etfs).toBe("number");
  expect(typeof body.summary.with_any_holdings).toBe("number");
});

test("GET /api/stats/holdings stats items have name and avg_weight", async ({ request }) => {
  const { stats } = await (await request.get("/api/stats/holdings")).json();
  if (stats.length === 0) return; // no holdings data — skip
  const item = stats[0];
  expect(item).toHaveProperty("name");
  expect(item).toHaveProperty("avg_weight");
  expect(item).toHaveProperty("etf_count");
  expect(typeof item.avg_weight).toBe("number");
});

test("GET /api/stats/holdings?begin=&end= date filter works", async ({ request }) => {
  const res = await request.get(
    "/api/stats/holdings?begin=20260501&end=20260518"
  );
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body).toHaveProperty("summary");
  expect(body.summary.total_etfs).toBeGreaterThanOrEqual(0);
});
