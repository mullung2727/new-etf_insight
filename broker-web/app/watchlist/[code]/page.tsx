import Link from "next/link";
import { brokerClient, type DailyCandle } from "@/lib/broker-client";
import { apiGet } from "@/lib/api-client";
import CandleChart from "@/components/watchlist/ChartLoader";
import BaseDatePicker from "@/components/watchlist/BaseDatePicker";

// 차트 윈도우: 기준일 앞 15거래일 ~ 뒤 20거래일
const DAYS_BEFORE = 15;
const DAYS_AFTER = 20;

type LlmScore = {
  date: string;
  ticker: string;
  name: string | null;
  score: number | null;
  category: string | null;
  reason_summary: string | null;
  final_opinion: string | null;
};

type ChartResult = {
  candles: DailyCandle[];
  markerDate: string; // 실제 마커가 찍히는 거래일(휴장 기준일이면 근사)
};

async function fetchScore(
  code: string,
  baseDate: string
): Promise<LlmScore | null> {
  try {
    const rows = await apiGet<LlmScore[]>(`/watchlist/scores/${baseDate}`);
    return rows.find((r) => r.ticker === code) ?? null;
  } catch (err) {
    console.error(`${code} 스코어 조회 에러:`, err);
    return null;
  }
}

function yyyymmdd(date: Date): string {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

function formatDate(raw: string): string {
  return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}

/**
 * ka10081은 base_dt 이전(과거)만 반환 → base_dt=오늘로 받아 최신까지 확보한 뒤,
 * 받은 캔들 배열(=실제 거래일 달력)에서 기준일 앞 15 ~ 뒤 20거래일을 잘라 쓴다.
 * 기준일이 최근이라 뒤 20일치가 없으면 배열 끝까지(=가장 최근 데이터).
 */
async function fetchChart(
  code: string,
  baseDate: string
): Promise<ChartResult | null> {
  try {
    const all = (await brokerClient.getDailyChart(code, yyyymmdd(new Date())))
      .slice()
      .sort((a, b) => a.dt.localeCompare(b.dt));
    if (all.length === 0) return { candles: [], markerDate: baseDate };

    // 기준일(휴장이면 그 이후 첫 거래일) 위치. 기준일이 데이터보다 미래면 마지막으로 클램프.
    let baseIdx = all.findIndex((c) => c.dt >= baseDate);
    if (baseIdx === -1) baseIdx = all.length - 1;

    const start = Math.max(0, baseIdx - DAYS_BEFORE);
    const end = Math.min(all.length, baseIdx + DAYS_AFTER + 1);
    return { candles: all.slice(start, end), markerDate: all[baseIdx].dt };
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

  const [chart, score] = await Promise.all([
    fetchChart(code, baseDate),
    fetchScore(code, baseDate),
  ]);

  const candles = chart?.candles ?? [];
  const rangeStart = candles.length > 0 ? candles[0].dt : null;
  const rangeEnd = candles.length > 0 ? candles[candles.length - 1].dt : null;

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
          <div className="flex items-center gap-3 flex-wrap">
            <BaseDatePicker code={code} baseDate={baseDate} name={name} />
            <p className="text-[13px] text-white/40 tracking-[0.15em] border-l-2 border-primary/30 pl-3">
              기준일 {formatDate(baseDate)}
              {rangeStart && rangeEnd && (
                <span className="text-white/30">
                  {" · "}
                  {formatDate(rangeStart)} — {formatDate(rangeEnd)}
                </span>
              )}
            </p>
          </div>
        </div>

        {candles.length > 0 ? (
          <CandleChart data={candles} baseDate={chart!.markerDate} />
        ) : (
          <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
            데이터를 불러올 수 없습니다
          </div>
        )}

        {score && (
          <div className="mt-8 border border-primary/20 rounded p-5">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-[11px] tracking-[0.2em] text-white/40">
                LLM SCORE · {formatDate(baseDate)}
              </span>
              {score.score != null && (
                <span className="text-xl font-bold text-primary">
                  {score.score}
                </span>
              )}
              {score.category && (
                <span className="text-[11px] tracking-[0.15em] text-white/50 border border-white/15 rounded px-2 py-0.5">
                  {score.category}
                </span>
              )}
            </div>
            {score.reason_summary && (
              <p className="text-[13px] text-white/70 leading-relaxed mb-3">
                {score.reason_summary}
              </p>
            )}
            {score.final_opinion && (
              <p className="text-[13px] text-white/50 leading-relaxed border-l-2 border-primary/30 pl-3">
                {score.final_opinion}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
