import { fetchCorpCodes, corpCodeForStock, fetchCompare } from "@/lib/dart";
import { brokerClient } from "@/lib/broker-client";
import CompareTable from "@/components/financial/CompareTable";
import RimPanel from "@/components/financial/RimPanel";

function Empty({ msg }: { msg: string }) {
  return (
    <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">{msg}</div>
  );
}

// 시가총액(억원) → 원. ka10001 mac 단위 억원, 재무제표 값(원)과 맞춤.
async function fetchMarketCapWon(code: string): Promise<number | null> {
  try {
    const mac = Number((await brokerClient.getQuote(code)).raw?.mac);
    return Number.isFinite(mac) && mac > 0 ? mac * 1e8 : null;
  } catch {
    return null; // 시총 실패는 괴리율만 미표시, RIM 자체는 계산
  }
}

// stock_code → corp_code 해석 후 다기간 재무 비교. 매핑/조회 실패는 빈상태.
// mode: annual(연 5년) | quarterly(분기 8개). 토글은 URL ?fin_mode= 로 서버 재렌더.
export default async function FinancialTab({
  code,
  mode = "annual",
}: {
  code: string;
  mode?: "annual" | "quarterly";
}) {
  let corpCode: string | null = null;
  try {
    corpCode = corpCodeForStock(await fetchCorpCodes(), code);
  } catch {
    return <Empty msg="기업 목록을 불러오지 못했습니다" />;
  }
  if (!corpCode) return <Empty msg="재무 데이터 없음 (DART 기업코드 매핑 실패)" />;

  try {
    const [data, marketCapWon] = await Promise.all([
      fetchCompare(corpCode, mode === "quarterly" ? 8 : 5, mode),
      fetchMarketCapWon(code),
    ]);
    return (
      <>
        <RimPanel data={data} marketCapWon={marketCapWon} />
        <CompareTable data={data} />
      </>
    );
  } catch {
    return <Empty msg="재무 데이터를 불러올 수 없습니다" />;
  }
}
