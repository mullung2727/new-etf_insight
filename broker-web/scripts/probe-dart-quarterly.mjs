/**
 * DART 분기 프로브 — v2 Step 0 게이트
 * 확인 2가지:
 *   (Q1) fnlttSinglIndx 가 분기(11013/11014) 지원하나? (status/list)
 *   (Q2) fnlttSinglAcntAll 분기 매출이 누적인가? (Q1 vs Q3 thstrm)
 * 대상: 삼성전자(대형·CFS), 바이브컴퍼니(소형·검증용)
 * 실행: node --env-file=.env.local scripts/probe-dart-quarterly.mjs
 */
import JSZip from "jszip";

const KEY = process.env.DART_API_KEY;
if (!KEY) { console.error("DART_API_KEY 없음"); process.exit(1); }
const BASE = "https://opendart.fss.or.kr/api";

async function get(path, params) {
  const url = `${BASE}/${path}?crtfc_key=${KEY}&${new URLSearchParams(params)}`;
  const res = await fetch(url);
  if (!res.ok) return { status: `HTTP ${res.status}`, list: [] };
  return res.json();
}

async function corpByStock(stockCode) {
  const res = await fetch(`${BASE}/corpCode.xml?crtfc_key=${KEY}`);
  const zip = await JSZip.loadAsync(await res.arrayBuffer());
  const xmlFile = Object.values(zip.files).find(f => f.name.endsWith(".xml"));
  const xml = await xmlFile.async("text");
  for (const item of xml.match(/<list>[\s\S]*?<\/list>/g) ?? []) {
    const sc = (item.match(/<stock_code>(.*?)<\/stock_code>/) ?? [])[1]?.trim();
    if (sc === stockCode)
      return {
        corp_code: (item.match(/<corp_code>(.*?)<\/corp_code>/) ?? [])[1],
        corp_name: (item.match(/<corp_name>(.*?)<\/corp_name>/) ?? [])[1],
      };
  }
  return null;
}

const REPRT = { Q1: "11013", H1: "11012", Q3: "11014", FY: "11011" };
const REVENUE = "ifrs-full_Revenue";

function revenue(data) {
  const it = (data.list ?? []).find(
    i => i.account_id === REVENUE && (i.sj_div === "IS" || i.sj_div === "CIS")
  );
  return it?.thstrm_amount ?? "(없음)";
}

async function probe(name, corp) {
  console.log(`\n===== ${name} (${corp}) 2024 =====`);
  // Q2: 금액 누적 여부 — 분기별 매출
  console.log("[금액 fnlttSinglAcntAll · fs_div=CFS] 매출액(thstrm):");
  for (const [tag, code] of Object.entries(REPRT)) {
    const d = await get("fnlttSinglAcntAll.json", {
      corp_code: corp, bsns_year: "2024", reprt_code: code, fs_div: "CFS",
    });
    console.log(`  ${tag}(${code}): status=${d.status} list=${d.list?.length ?? 0} 매출=${revenue(d)}`);
  }
  // Q1: 비율 분기 지원 여부 — ROE 분류(M210000)
  console.log("[비율 fnlttSinglIndx · idx_cl=M210000(수익성)]:");
  for (const [tag, code] of Object.entries(REPRT)) {
    const d = await get("fnlttSinglIndx.json", {
      corp_code: corp, bsns_year: "2024", reprt_code: code, idx_cl_code: "M210000",
    });
    console.log(`  ${tag}(${code}): status=${d.status} list=${d.list?.length ?? 0}`);
  }
}

const vaiv = await corpByStock("301300");
console.log(`바이브컴퍼니 corp_code: ${vaiv?.corp_code} name: ${vaiv?.corp_name}`);

await probe("삼성전자", "00126380");
if (vaiv) await probe("바이브컴퍼니", vaiv.corp_code);

console.log("\n판정:");
console.log("  Q1 답 = 비율 블록 Q1/H1/Q3 status가 000이고 list>0 이면 분기지원.");
console.log("  Q2 답 = 매출 Q1 < H1 < Q3 로 증가하면 누적, 아니면 단독.");
