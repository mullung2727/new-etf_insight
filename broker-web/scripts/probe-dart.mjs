/**
 * DART API 실호출 프로브 스크립트
 * 용도: account_id / idx_code / idx_cl_code 실값 확정 + 픽스처 저장
 * 실행: node --env-file=.env.local scripts/probe-dart.mjs
 */

import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dir = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(__dir, "../__tests__/fixtures");
mkdirSync(FIXTURES, { recursive: true });

const KEY = process.env.DART_API_KEY;
if (!KEY) { console.error("DART_API_KEY 없음"); process.exit(1); }

const BASE = "https://opendart.fss.or.kr/api";

async function get(path, params) {
  const url = `${BASE}/${path}?crtfc_key=${KEY}&${new URLSearchParams(params)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.json();
}

function save(name, data) {
  const p = join(FIXTURES, name);
  writeFileSync(p, JSON.stringify(data, null, 2), "utf8");
  console.log(`saved: ${name}  status=${data.status}  list=${data.list?.length ?? "N/A"}`);
}

// ── 1. M83 corp_code 탐색 (stock_code=476080) ─────────────────────────────
console.log("\n[1] M83 corp_code 탐색...");
const compRes = await fetch(
  `${BASE}/company.json?crtfc_key=${KEY}&corp_code=00000000`
).then(r => r.json()).catch(() => null);

// corpCode.xml 대신 stock_code로 검색할 방법 없음 → 직접 API로 검색
// DART company.json은 corp_code 8자리 필수 → corpCode.xml 파싱 필요
// 대신: fnlttSinglAcntAll에 well-known corp_code를 시도하거나 DART 웹에서 확인
// M83(476080) DART corp_code는 스크립트 실행 전 아래에서 corpCode.xml로 찾음

async function findCorpCodeByStock(stockCode) {
  const res = await fetch(`${BASE}/corpCode.xml?crtfc_key=${KEY}`);
  if (!res.ok) throw new Error(`corpCode.xml HTTP ${res.status}`);
  const buf = await res.arrayBuffer();

  // ZIP 파싱 없이 node:stream으로 처리하려면 jszip 필요
  // 여기선 별도 import 없이 동적 import
  const { default: JSZip } = await import("jszip");
  const zip = await JSZip.loadAsync(buf);
  const xmlFile = Object.values(zip.files).find(f => f.name.endsWith(".xml"));
  const xml = await xmlFile.async("text");

  const items = xml.match(/<list>[\s\S]*?<\/list>/g) ?? [];
  for (const item of items) {
    const sc = (item.match(/<stock_code>(.*?)<\/stock_code>/) ?? [])[1] ?? "";
    if (sc.trim() === stockCode) {
      const cc = (item.match(/<corp_code>(.*?)<\/corp_code>/) ?? [])[1] ?? "";
      const cn = (item.match(/<corp_name>(.*?)<\/corp_name>/) ?? [])[1] ?? "";
      return { corp_code: cc, corp_name: cn };
    }
  }
  return null;
}

const m83Info = await findCorpCodeByStock("476080");
if (!m83Info) { console.error("M83 corp_code 찾기 실패"); process.exit(1); }
console.log(`M83 corp_code: ${m83Info.corp_code}  name: ${m83Info.corp_name}`);
writeFileSync(join(FIXTURES, "m83-corp-info.json"), JSON.stringify(m83Info, null, 2), "utf8");

const SAMSUNG = "00126380";
const M83 = m83Info.corp_code;

// ── 2. 삼성전자 fnlttSinglAcntAll 2024 CFS ────────────────────────────────
console.log("\n[2] 삼성전자 fnlttSinglAcntAll 2024 CFS...");
const samsungAcnt = await get("fnlttSinglAcntAll.json", {
  corp_code: SAMSUNG, bsns_year: "2024", reprt_code: "11011", fs_div: "CFS",
});
save("acnt-samsung-2024-cfs.json", samsungAcnt);

// ── 3. 삼성전자 fnlttSinglIndx 2024 수익성·안정성·성장성 ──────────────────
console.log("\n[3] 삼성전자 fnlttSinglIndx 2024 idx_cl_code 3개...");
const IDX_CODES = ["M210000", "M220000", "M230000"]; // 추정치 — 실값 확인
for (const code of IDX_CODES) {
  const data = await get("fnlttSinglIndx.json", {
    corp_code: SAMSUNG, bsns_year: "2024", reprt_code: "11011", idx_cl_code: code,
  });
  save(`indx-samsung-2024-${code}.json`, data);
}

// ── 4. M83 fnlttSinglAcntAll 2025 CFS (013 확인) ─────────────────────────
console.log("\n[4] M83 fnlttSinglAcntAll 2025 CFS (013 기대)...");
const m83Acnt2025 = await get("fnlttSinglAcntAll.json", {
  corp_code: M83, bsns_year: "2025", reprt_code: "11011", fs_div: "CFS",
});
save("acnt-m83-2025-cfs.json", m83Acnt2025);

// ── 5. M83 fnlttSinglAcntAll 2025 OFS (013 확인) ─────────────────────────
console.log("\n[5] M83 fnlttSinglAcntAll 2025 OFS (013 기대)...");
const m83Acnt2025Ofs = await get("fnlttSinglAcntAll.json", {
  corp_code: M83, bsns_year: "2025", reprt_code: "11011", fs_div: "OFS",
});
save("acnt-m83-2025-ofs.json", m83Acnt2025Ofs);

// ── 6. M83 fnlttSinglAcntAll 2024 CFS (성공 기대) ────────────────────────
console.log("\n[6] M83 fnlttSinglAcntAll 2024 CFS (성공 기대)...");
const m83Acnt2024 = await get("fnlttSinglAcntAll.json", {
  corp_code: M83, bsns_year: "2024", reprt_code: "11011", fs_div: "CFS",
});
save("acnt-m83-2024-cfs.json", m83Acnt2024);

// ── 7. 삼성전자 account_id 추출 요약 ────────────────────────────────────
console.log("\n[7] 삼성전자 계정 목록 (account_id · account_nm):");
if (samsungAcnt.list) {
  const seen = new Set();
  for (const item of samsungAcnt.list) {
    const key = `${item.account_id} | ${item.account_nm} | sj_div=${item.sj_div}`;
    if (!seen.has(key)) { seen.add(key); console.log("  ", key); }
  }
}

// ── 8. 삼성전자 idx 추출 요약 ────────────────────────────────────────────
for (const code of IDX_CODES) {
  const raw = JSON.parse(
    (await import("fs")).readFileSync(join(FIXTURES, `indx-samsung-2024-${code}.json`), "utf8")
  );
  console.log(`\n[idx ${code}] status=${raw.status}`);
  if (raw.list) {
    for (const item of raw.list) {
      console.log(`  idx_code=${item.idx_code} | idx_nm=${item.idx_nm} | val=${item.idx_val}`);
    }
  }
}

console.log("\n완료. __tests__/fixtures/ 확인.");
