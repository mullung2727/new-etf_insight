import { redirect } from "next/navigation";
import { stockHubHref } from "@/lib/stock-hub";

// 기존 /watchlist/[code] 차트 페이지 → 종목 개괄 허브 차트 탭으로 이관.
export default async function WatchlistCodeRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ code: string }>;
  searchParams: Promise<{ date?: string; name?: string }>;
}) {
  const { code } = await params;
  const { date, name } = await searchParams;
  redirect(stockHubHref(code, { tab: "chart", date, name }));
}
