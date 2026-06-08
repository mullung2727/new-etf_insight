import JSZip from "jszip";
import { CompanyInfo, CorpCode, FinancialResponse, FsDivType, ReprtCode } from "@/types/dart";

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
