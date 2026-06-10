import JSZip from "jszip";
import {
  CompanyInfo, CorpCode, FinancialResponse, FsDivType, ReprtCode,
  FinancialIndexResponse, CompareResponse, CompareRow,
} from "@/types/dart";
import { AMOUNT_ACCOUNTS, RATIO_INDICES } from "@/lib/dart-compare-keys";

const BASE_URL = "https://opendart.fss.or.kr";

function getApiKey(): string {
  const key = process.env.DART_API_KEY;
  if (!key) throw new Error("DART_API_KEY 환경변수가 설정되지 않았습니다.");
  return key;
}

export async function searchCompany(corpCode: string): Promise<CompanyInfo> {
  try {
    const key = getApiKey();
    const url = `${BASE_URL}/api/company.json?crtfc_key=${key}&corp_code=${corpCode}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.status !== "000") throw new Error(data.message);
    return data as CompanyInfo;
  } catch (err) {
    throw new Error(`기업 검색 실패: ${err instanceof Error ? err.message : String(err)}`);
  }
}

export async function fetchFinancial(
  corpCode: string,
  bsnsYear: string,
  reprtCode: ReprtCode,
  fsDiv: FsDivType
): Promise<FinancialResponse> {
  try {
    const key = getApiKey();
    const url =
      `${BASE_URL}/api/fnlttSinglAcntAll.json` +
      `?crtfc_key=${key}&corp_code=${corpCode}&bsns_year=${bsnsYear}` +
      `&reprt_code=${reprtCode}&fs_div=${fsDiv}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data: FinancialResponse = await res.json();
    if (data.status !== "000") throw new Error(data.message);
    return data;
  } catch (err) {
    throw new Error(`재무제표 조회 실패: ${err instanceof Error ? err.message : String(err)}`);
  }
}

// ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

async function fetchAcntRaw(
  corpCode: string,
  year: string,
  fsDiv: FsDivType
): Promise<FinancialResponse> {
  try {
    const key = getApiKey();
    const url =
      `${BASE_URL}/api/fnlttSinglAcntAll.json` +
      `?crtfc_key=${key}&corp_code=${corpCode}&bsns_year=${year}&reprt_code=11011&fs_div=${fsDiv}`;
    const res = await fetch(url);
    if (!res.ok) return { status: "ERR", message: `HTTP ${res.status}`, list: [] };
    return res.json();
  } catch {
    return { status: "ERR", message: "fetch failed", list: [] };
  }
}

async function fetchIndxRaw(
  corpCode: string,
  year: string,
  idxClCode: string
): Promise<FinancialIndexResponse> {
  try {
    const key = getApiKey();
    const url =
      `${BASE_URL}/api/fnlttSinglIndx.json` +
      `?crtfc_key=${key}&corp_code=${corpCode}&bsns_year=${year}&reprt_code=11011&idx_cl_code=${idxClCode}`;
    const res = await fetch(url);
    if (!res.ok) return { status: "ERR", message: `HTTP ${res.status}`, list: [] };
    return res.json();
  } catch {
    return { status: "ERR", message: "fetch failed", list: [] };
  }
}

function extractAmount(
  list: FinancialResponse["list"],
  accountId: string,
  sjDiv: string
): number | null {
  // 일부 기업(단일 포괄손익계산서)은 IS 없이 CIS에만 IS 계정이 있음 → CIS 폴백
  let item = list.find(i => i.account_id === accountId && i.sj_div === sjDiv);
  if (!item && sjDiv === "IS") {
    item = list.find(i => i.account_id === accountId && i.sj_div === "CIS");
  }
  // 금융지주: dart_OperatingIncomeLoss 대신 ifrs-full_ProfitLossFromOperatingActivities[CIS] 사용
  // 신한(2023/2024), KB(2024), 하나(2023/2024) 실호출 확인 (2026-06-10)
  if (!item && accountId === "dart_OperatingIncomeLoss") {
    item = list.find(i =>
      i.account_id === "ifrs-full_ProfitLossFromOperatingActivities" &&
      (i.sj_div === sjDiv || i.sj_div === "CIS")
    );
  }
  if (!item?.thstrm_amount) return null;
  const n = parseFloat(String(item.thstrm_amount).replace(/,/g, ""));
  return isNaN(n) ? null : n;
}

function extractRatio(
  list: FinancialIndexResponse["list"],
  idxCode: string
): number | null {
  const item = list?.find(i => i.idx_code === idxCode);
  if (!item?.idx_val) return null;
  const n = parseFloat(item.idx_val);
  return isNaN(n) ? null : n;
}

async function determineFsDiv(corpCode: string): Promise<{
  baseYear: number;
  fsDiv: FsDivType;
  baseData: FinancialResponse;
}> {
  let year = new Date().getFullYear() - 1;
  for (let attempt = 0; attempt < 3; attempt++, year--) {
    for (const fsDiv of ["CFS", "OFS"] as FsDivType[]) {
      const data = await fetchAcntRaw(corpCode, String(year), fsDiv);
      if (data.status === "000" && data.list.length > 0) {
        return { baseYear: year, fsDiv, baseData: data };
      }
    }
  }
  throw new Error(`${corpCode}: 유효한 사업보고서를 찾지 못했습니다.`);
}

// ── fetchCompare ──────────────────────────────────────────────────────────────

export async function fetchCompare(
  corpCode: string,
  years = 5
): Promise<CompareResponse> {
  const { baseYear, fsDiv, baseData } = await determineFsDiv(corpCode);
  const periods = Array.from({ length: years }, (_, i) => baseYear - years + 1 + i);

  // 금액: 각 연도 병렬 호출 (기준 연도는 determineFsDiv 결과 재사용)
  const acntResults = await Promise.all(
    periods.map(year =>
      year === baseYear
        ? Promise.resolve(baseData)
        : fetchAcntRaw(corpCode, String(year), fsDiv)
    )
  );

  // 비율: 2023+ 연도 × 3개 분류 병렬 호출
  const ratioYears = periods.filter(y => y >= 2023);
  const ratioMap = new Map<number, Map<string, FinancialIndexResponse>>();

  await Promise.all(
    ratioYears.flatMap(year =>
      RATIO_INDICES.map(async ri => {
        const data = await fetchIndxRaw(corpCode, String(year), ri.idx_cl_code);
        if (!ratioMap.has(year)) ratioMap.set(year, new Map());
        ratioMap.get(year)!.set(ri.idx_cl_code, data);
      })
    )
  );

  // 금액 행
  const amountRows: CompareRow[] = AMOUNT_ACCOUNTS.map(acnt => ({
    key: acnt.key,
    label: acnt.label,
    type: "amount",
    values: acntResults.map(res =>
      res.status === "000" ? extractAmount(res.list, acnt.account_id, acnt.sj_div) : null
    ),
  }));

  // 영업이익률 계산
  const revenues = amountRows.find(r => r.key === "revenue")!.values;
  const opProfits = amountRows.find(r => r.key === "opProfit")!.values;
  const opMarginValues: (number | null)[] = periods.map((_, i) => {
    const rev = revenues[i];
    const op = opProfits[i];
    if (rev == null || op == null || rev === 0) return null;
    return Math.round((op / rev) * 10000) / 100;
  });

  // 비율 행
  const ratioRows: CompareRow[] = [
    { key: "opMargin", label: "영업이익률", type: "ratio", values: opMarginValues },
    ...RATIO_INDICES.map(ri => ({
      key: ri.key,
      label: ri.label,
      type: "ratio" as const,
      values: periods.map(year => {
        const yearMap = ratioMap.get(year);
        if (!yearMap) return null;
        const res = yearMap.get(ri.idx_cl_code);
        return extractRatio(res?.list, ri.idx_code);
      }),
    })),
  ];

  const corpInfo = await searchCompany(corpCode);

  return {
    corpName: corpInfo.corp_name,
    fsDiv,
    periods,
    rows: [...amountRows, ...ratioRows],
  };
}

export async function fetchCorpCodes(): Promise<CorpCode[]> {
  try {
    const key = getApiKey();
    const url = `${BASE_URL}/api/corpCode.xml?crtfc_key=${key}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const buffer = await res.arrayBuffer();
    const zip = await JSZip.loadAsync(buffer);
    const xmlFile = Object.values(zip.files).find((f) => f.name.endsWith(".xml"));
    if (!xmlFile) throw new Error("ZIP에서 XML 파일을 찾을 수 없습니다.");

    const xml = await xmlFile.async("text");
    const items = xml.match(/<list>[\s\S]*?<\/list>/g) ?? [];
    const decode = (s: string) =>
      s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'");

    return items.map((item) => ({
      corp_code: (item.match(/<corp_code>(.*?)<\/corp_code>/) ?? [])[1] ?? "",
      corp_name: decode((item.match(/<corp_name>(.*?)<\/corp_name>/) ?? [])[1] ?? ""),
      stock_code: (item.match(/<stock_code>(.*?)<\/stock_code>/) ?? [])[1] ?? "",
    }));
  } catch (err) {
    throw new Error(`기업 목록 조회 실패: ${err instanceof Error ? err.message : String(err)}`);
  }
}
