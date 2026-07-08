import JSZip from "jszip";
import {
  CompanyInfo, CorpCode, FinancialResponse, FsDivType, ReprtCode,
  FinancialIndexResponse, CompareResponse, CompareRow,
} from "@/types/dart";
import { AMOUNT_ACCOUNTS, RATIO_INDICES } from "@/lib/dart-compare-keys";
import {
  AmountField, BsRowDef, selectBsTopAccounts, extractBsRowAmount,
} from "@/lib/dart-bs-topn";
import { buildPeriods, QUARTER_REPRT } from "@/lib/dart-periods";
import type { CompareMode, PeriodDesc, Quarter } from "@/lib/dart-periods";
import { deriveQ4List } from "@/lib/dart-quarterly";

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

// ── 분기 모드 헬퍼 ────────────────────────────────────────────────────────────

/** 오늘 기준 최신 "제출 가능성 높은" 분기 추정 (DART 제출기한 대략치).
 *  실제 가용은 determineQuarterlyBase 가 probe 로 확정. */
function estimateLatestQuarter(d: Date): { year: number; quarter: Quarter } {
  const y = d.getFullYear();
  const m = d.getMonth() + 1;
  if (m >= 11) return { year: y, quarter: 3 };   // Q3 ~11월 제출
  if (m >= 8)  return { year: y, quarter: 2 };    // 반기 ~8월
  if (m >= 5)  return { year: y, quarter: 1 };    // Q1 ~5월
  return { year: y - 1, quarter: 4 };             // 1~4월: 전년 FY(파생 Q4)
}

/** 추정 기준점에서 뒤로 probe 하며 실제 데이터 있는 최신 분기 확정. */
async function determineQuarterlyBase(
  corpCode: string,
  fsDiv: FsDivType
): Promise<{ year: number; quarter: Quarter }> {
  let { year, quarter } = estimateLatestQuarter(new Date());
  for (let attempt = 0; attempt < 5; attempt++) {
    const data = await fetchAcntRaw(corpCode, String(year), fsDiv, QUARTER_REPRT[quarter]);
    if (data.status === "000" && data.list.length > 0) return { year, quarter };
    if (quarter === 1) { quarter = 4; year -= 1; } else { quarter = (quarter - 1) as Quarter; }
  }
  throw new Error(`${corpCode}: 유효한 분기보고서를 찾지 못했습니다.`);
}

/** 기간 descs 를 채우는 금액 응답 배열(오름차순 정렬).
 *  연도 단위로 4보고서(11013/12/14/11011) 통째 fetch → window 경계와 무관하게
 *  Q4 파생에 필요한 같은해 Q1~Q3 를 항상 확보. */
async function fetchQuarterlyAcnt(
  corpCode: string,
  fsDiv: FsDivType,
  descs: PeriodDesc[]
): Promise<FinancialResponse[]> {
  const years = [...new Set(descs.map(d => d.year))];
  const byYear = new Map<number, { q1: FinancialResponse; q2: FinancialResponse; q3: FinancialResponse; fy: FinancialResponse }>();
  await Promise.all(
    years.map(async y => {
      const [q1, q2, q3, fy] = await Promise.all([
        fetchAcntRaw(corpCode, String(y), fsDiv, "11013"),
        fetchAcntRaw(corpCode, String(y), fsDiv, "11012"),
        fetchAcntRaw(corpCode, String(y), fsDiv, "11014"),
        fetchAcntRaw(corpCode, String(y), fsDiv, "11011"),
      ]);
      byYear.set(y, { q1, q2, q3, fy });
    })
  );

  const ok = (r: FinancialResponse) => r.status === "000";
  return descs.map(d => {
    const y = byYear.get(d.year)!;
    switch (d.quarter) {
      case 1: return y.q1;
      case 2: return y.q2;
      case 3: return y.q3;
      default: // Q4 = FY − (Q1+Q2+Q3), 넷 다 있어야 파생
        return ok(y.fy) && ok(y.q1) && ok(y.q2) && ok(y.q3)
          ? { status: "000", message: "", list: deriveQ4List(y.fy, y.q1, y.q2, y.q3) }
          : { status: "ERR", message: "Q4 파생 불가", list: [] };
    }
  });
}

// ── fetchCompare ──────────────────────────────────────────────────────────────

export async function fetchCompare(
  corpCode: string,
  count = 5,
  mode: CompareMode = "annual"
): Promise<CompareResponse> {
  const { baseYear, fsDiv, baseData } = await determineFsDiv(corpCode);
  const n = count;

  // 모드별로 아래 4개를 채운다:
  //   periodLabels — 기간 라벨, resolve — (추출함수→기간별 값 배열),
  //   topNList — BS top-N 선정 기준 list, ratioByIndex — 기간 index→비율 응답
  let periodLabels: string[];
  let resolve: (
    extract: (list: FinancialResponse["list"], field: AmountField) => number | null
  ) => (number | null)[];
  let topNList: FinancialResponse["list"];
  const ratioByIndex = new Map<number, Map<string, FinancialIndexResponse>>();

  if (mode === "annual") {
    const periods = Array.from({ length: n }, (_, i) => baseYear - n + 1 + i);
    // 금액: 각 연도 병렬 (기준 연도는 determineFsDiv 결과 재사용)
    const acntResults = await Promise.all(
      periods.map(year =>
        year === baseYear ? Promise.resolve(baseData) : fetchAcntRaw(corpCode, String(year), fsDiv)
      )
    );
    // 직접 조회 실패 연도(status≠000)는 이후 연도 보고서의 전기/전전기로 백필
    resolve = (extract) =>
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
    topNList = baseData.list;
    // 비율: 2023+ 연도 × 3분류, 기간 index 로 저장
    await Promise.all(
      periods.flatMap((year, i) =>
        year < 2023 ? [] : RATIO_INDICES.map(async ri => {
          const data = await fetchIndxRaw(corpCode, String(year), ri.idx_cl_code);
          if (!ratioByIndex.has(i)) ratioByIndex.set(i, new Map());
          ratioByIndex.get(i)!.set(ri.idx_cl_code, data);
        })
      )
    );
    periodLabels = periods.map(String);
  } else {
    const base = await determineQuarterlyBase(corpCode, fsDiv);
    const descs = buildPeriods("quarterly", n, base);
    const acntResults = await fetchQuarterlyAcnt(corpCode, fsDiv, descs);
    // 분기 thstrm은 이미 단독값 → 백필 없이 직접 추출 (Q4는 파생 응답)
    resolve = (extract) =>
      acntResults.map(res => (res.status === "000" ? extract(res.list, "thstrm") : null));
    // BS top-N 기준 = 최신 유효 분기 응답
    topNList = acntResults.slice().reverse().find(r => r.status === "000")?.list ?? baseData.list;
    // 비율: 2023+ 기간 × 3분류 (Q4는 reprt 11011=연간 비율)
    await Promise.all(
      descs.flatMap((d, i) =>
        d.year < 2023 ? [] : RATIO_INDICES.map(async ri => {
          const data = await fetchIndxRaw(corpCode, String(d.year), ri.idx_cl_code, d.reprtCode);
          if (!ratioByIndex.has(i)) ratioByIndex.set(i, new Map());
          ratioByIndex.get(i)!.set(ri.idx_cl_code, data);
        })
      )
    );
    periodLabels = descs.map(d => d.label);
  }

  // 고정 계정 (IS 3개 + BS 총계 3개)
  const amountValues = new Map<string, (number | null)[]>(
    AMOUNT_ACCOUNTS.map(acnt => [
      acnt.key,
      resolve((list, field) =>
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
  const topDefs = selectBsTopAccounts(topNList);
  const dynRows = (defs: BsRowDef[], prefix: string): CompareRow[] =>
    defs.map((def, i) => ({
      key: `${prefix}${i}`,
      label: def.nm,
      type: "amount",
      section: "bs",
      values: resolve((list, field) => extractBsRowAmount(list, def, field)),
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
  const opMarginValues: (number | null)[] = Array.from({ length: n }, (_, i) => {
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
      values: Array.from({ length: n }, (_, i) => {
        const m = ratioByIndex.get(i);
        if (!m) return null;
        const res = m.get(ri.idx_cl_code);
        return extractRatio(res?.list, ri.idx_code);
      }),
    })),
  ];

  const corpInfo = await searchCompany(corpCode);

  return {
    corpName: corpInfo.corp_name,
    fsDiv,
    periods: periodLabels,
    rows: [...bsRows, ...isRows, ...ratioRows],
  };
}

// stock_code(6자리 상장코드) → corp_code(DART 8자리). 미상장/미매칭은 null.
// 빈 입력은 CorpCode[]의 빈 stock_code 행(비상장)과 오매칭되면 안 되므로 가드.
export function corpCodeForStock(corps: CorpCode[], stockCode: string): string | null {
  const code = stockCode.trim();
  if (!code) return null;
  return corps.find((c) => c.stock_code === code)?.corp_code ?? null;
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
