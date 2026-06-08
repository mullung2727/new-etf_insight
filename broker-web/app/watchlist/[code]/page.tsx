import Link from "next/link";
import { brokerClient, type DailyCandle } from "@/lib/broker-client";
import CandleChart from "@/components/watchlist/ChartLoader";

function yyyymmdd(date: Date): string {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

function subtractDays(dateStr: string, days: number): string {
  const d = new Date(
    +dateStr.slice(0, 4),
    +dateStr.slice(4, 6) - 1,
    +dateStr.slice(6, 8)
  );
  d.setDate(d.getDate() - days);
  return yyyymmdd(d);
}

function formatDate(raw: string): string {
  return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}

async function fetchChart(
  code: string,
  baseDate: string,
  startDate: string,
  endDate: string
): Promise<DailyCandle[] | null> {
  try {
    const all = await brokerClient.getDailyChart(code, baseDate);
    return all
      .filter((item) => item.dt >= startDate && item.dt <= endDate)
      .sort((a, b) => a.dt.localeCompare(b.dt));
  } catch (err) {
    console.error(`${code} 차트 조회 에러:`, err);
    return null;
  }
}

export default async function ChartPage({
  params,
  searchParams,
}: {
  params: Promise<{ code: string }>;
  searchParams: Promise<{ date?: string; name?: string }>;
}) {
  const { code } = await params;
  const { date, name } = await searchParams;

  const baseDate = date ?? yyyymmdd(new Date());
  const startDate = subtractDays(baseDate, 14);
  const endDate = yyyymmdd(new Date());

  const data = await fetchChart(code, baseDate, startDate, endDate);

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      {/* 배경 그리드 */}
      <div className="fixed inset-0 pointer-events-none bg-grid" />

      <div className="relative max-w-[1200px] mx-auto px-6 py-10">
        <Link href="/watchlist" className="text-white/50 text-[13px] tracking-[0.15em] no-underline">
          ← WATCHLIST
        </Link>

        <div className="mt-6 mb-8">
          <div className="flex items-baseline gap-3 mb-2">
            <h1 className="text-2xl font-bold text-primary tracking-[0.05em]">
              {name ?? code}
            </h1>
            <span className="text-xs text-white/50">{code}</span>
          </div>
          <p className="text-[13px] text-white/40 tracking-[0.15em] border-l-2 border-primary/30 pl-3">
            기준일 {formatDate(baseDate)} · {formatDate(startDate)} — {formatDate(endDate)}
          </p>
        </div>

        {data && data.length > 0 ? (
          <CandleChart data={data} baseDate={baseDate} />
        ) : (
          <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
            데이터를 불러올 수 없습니다
          </div>
        )}
      </div>
    </div>
  );
}
