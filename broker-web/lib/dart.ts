import JSZip from "jszip";
import {
  CompanyInfo, CorpCode, FinancialResponse, FsDivType, ReprtCode,
  FinancialIndexResponse, CompareResponse, CompareRow,
} from "@/types/dart";
import { AMOUNT_ACCOUNTS, RATIO_INDICES } from "@/lib/dart-compare-keys";
import {
  AmountField, BsRowDef, selectBsTopAccounts, extractBsRowAmount,
} from "@/lib/dart-bs-topn";

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
  fsDiv: FsDivType,
  reprtCode: ReprtCode = "11011"
): Promise<FinancialResponse> {
  try {
    const key = getApiKey();
    const url =
      `${BASE_URL}/api/fnlttSinglAcntAll.json` +
      `?crtfc_key=${key}&corp_code=${corpCode}&bsns_year=${year}&reprt_code=${reprtCode}&fs_div=${fsDiv}`;
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
  idxClCode: string,
  reprtCode: ReprtCode = "11011"
): Promise<FinancialIndexResponse> {
  try {
    const key = getApiKey();
    const url =
      `${BASE_URL}/api/fnlttSinglIndx.json` +
      `?crtfc_key=${key}&corp_code=${corpCode}&bsns_year=${year}&reprt_code=${reprtCode}&idx_cl_code=${idxClCode}`;
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
  sjDiv: string,
  field: AmountField = "thstrm"
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
  const amount = item?.[`${field}_amount`];
  if (!amount) return null;
  const n = parseFloat(String(amount).replace(/,/g, ""));
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

  // 연도별 금액 — 직접 조회 실패 연도(status≠000)는 이후 연도 보고서의
  // 전기(frmtrm)/전전기(bfefrmtrm) 금액으로 백필 (예: 금융지주 2021/2022)
  const valuesWithBackfill = (
    extract: (list: FinancialResponse["list"], field: AmountField) => number | null
  ): (number | null)[] =>
    acntResults.map((res, i) => {
      if (res.status === "000") return extract(res.list, "thstrm");
      const backfills: { donor: FinancialResponse | undefined; field: AmountField }[] = [
        { donor: acntResults[i + 1], field: "frmtrm" },
        { donor: acntResults[i + 2], field: "bfefrmtrm" },
      ];
      for (const { donor, field } of backfills) {
        if (donor?.status !== "000") continue;
        const v = extract(donor.list, field);
        if (v !== null) return v;
      }
      return null;
    });

  // 고정 계정 (IS 3개 + BS 총계 3개)
  const amountValues = new Map<string, (number | null)[]>(
    AMOUNT_ACCOUNTS.map(acnt => [
      acnt.key,
      valuesWithBackfill((list, field) =>
        extractAmount(list, acnt.account_id, acnt.sj_div, field)
      ),
    ])
  );
  const amountRow = (key: string, section: "bs" | "is"): CompareRow => ({
    key,
    label: AMOUNT_ACCOUNTS.find(a => a.key === key)!.label,
    type: "amount",
    section,
    values: amountValues.get(key)!,
  });

  // BS 동적 행 — 최신 연도 기준 자산 top5 / 부채 top3 선정 (docs/PLAN_FINANCIAL_BS_TOPN.md)
  const topDefs = selectBsTopAccounts(baseData.list);
  const dynRows = (defs: BsRowDef[], prefix: string): CompareRow[] =>
    defs.map((def, i) => ({
      key: `${prefix}${i}`,
      label: def.nm,
      type: "amount",
      section: "bs",
      values: valuesWithBackfill((list, field) => extractBsRowAmount(list, def, field)),
    }));
  const assetRows = dynRows(topDefs.assets, "bsAsset");
  const liabRows = dynRows(topDefs.liabilities, "bsLiab");

  // 그 외 = 총계 − top 행 합 (총계 null이면 null, top 행 null은 0 취급)
  const etcValues = (totalKey: string, topRows: CompareRow[]): (number | null)[] =>
    amountValues.get(totalKey)!.map((tot, i) =>
      tot == null ? null : tot - topRows.reduce((s, r) => s + (r.values[i] ?? 0), 0)
    );

  // 자본 — 표준 고정 행 (방안 2, docs/PLAN_FINANCIAL_BS_TOPN.md)
  // 자본은 음수 항목(자본조정 등)·2단 중첩 때문에 top-N 부적합 → 자본금/이익잉여금/비지배 고정
  const equityCapitalRow = amountRow("equityCapital", "bs");
  const equityRetainedRow = amountRow("equityRetained", "bs");
  const equityNciRow = amountRow("equityNci", "bs");

  const bsRows: CompareRow[] = [
    ...assetRows,
    { key: "bsAssetEtc", label: "그 외 자산", type: "amount", section: "bs", values: etcValues("totalAssets", assetRows) },
    amountRow("totalAssets", "bs"),
    ...liabRows,
    { key: "bsLiabEtc", label: "그 외 부채", type: "amount", section: "bs", values: etcValues("totalLiab", liabRows) },
    amountRow("totalLiab", "bs"),
    equityCapitalRow,
    equityRetainedRow,
    {
      key: "equityEtc", label: "기타(자본)", type: "amount", section: "bs",
      values: etcValues("totalEquity", [equityCapitalRow, equityRetainedRow, equityNciRow]),
    },
    equityNciRow,
    amountRow("totalEquity", "bs"),
  ];

  const isRows: CompareRow[] = [
    amountRow("revenue", "is"),
    amountRow("opProfit", "is"),
    amountRow("netIncome", "is"),
  ];

  // 영업이익률 계산
  const revenues = amountValues.get("revenue")!;
  const opProfits = amountValues.get("opProfit")!;
  const opMarginValues: (number | null)[] = periods.map((_, i) => {
    const rev = revenues[i];
    const op = opProfits[i];
    if (rev == null || op == null || rev === 0) return null;
    return Math.round((op / rev) * 10000) / 100;
  });

  // 비율 행
  const ratioRows: CompareRow[] = [
    { key: "opMargin", label: "영업이익률", type: "ratio", section: "ratio", values: opMarginValues },
    ...RATIO_INDICES.map(ri => ({
      key: ri.key,
      label: ri.label,
      type: "ratio" as const,
      section: "ratio" as const,
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
    periods: periods.map(String),
    rows: [...bsRows, ...isRows, ...ratioRows],
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
