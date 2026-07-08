import { fetchCorpCodes, corpCodeForStock, fetchCompare } from "@/lib/dart";
import CompareTable from "@/components/financial/CompareTable";

function Empty({ msg }: { msg: string }) {
  return (
    <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">{msg}</div>
  );
}

// stock_code → corp_code 해석 후 다기간(연 5년) 재무 비교. 매핑/조회 실패는 빈상태.
export default async function FinancialTab({ code }: { code: string }) {
  let corpCode: string | null = null;
  try {
    corpCode = corpCodeForStock(await fetchCorpCodes(), code);
  } catch {
    return <Empty msg="기업 목록을 불러오지 못했습니다" />;
  }
  if (!corpCode) return <Empty msg="재무 데이터 없음 (DART 기업코드 매핑 실패)" />;

  try {
    const data = await fetchCompare(corpCode, 5, "annual");
    return <CompareTable data={data} />;
  } catch {
    return <Empty msg="재무 데이터를 불러올 수 없습니다" />;
  }
}
