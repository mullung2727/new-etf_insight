import { brokerClient, type DailyCandle } from "@/lib/broker-client";
import CandleChart from "@/components/watchlist/ChartLoader";
import BaseDatePicker from "@/components/watchlist/BaseDatePicker";
import ChartRangePicker from "@/components/stock/chart-range-picker";
import { sliceByRange, type ChartRange } from "@/lib/chart-range";

type ChartResult = {
  candles: DailyCandle[];
  markerDate: string; // baseDate가 구간 안이면 그 거래일, 아니면 구간 첫 거래일
};

function yyyymmdd(date: Date): string {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

function formatDate(raw: string): string {
  return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}

/**
 * getDailyChart(base_dt=오늘)로 전체 히스토리를 받아 최신순 정렬 후,
 * 선택 기간(range)만큼 최근 거래일을 잘라 쓴다. baseDate는 마커 위치.
 */
async function fetchChart(code: string, baseDate: string, range: ChartRange): Promise<ChartResult | null> {
  try {
    const all = (await brokerClient.getDailyChart(code, yyyymmdd(new Date())))
      .slice()
      .sort((a, b) => a.dt.localeCompare(b.dt));
    if (all.length === 0) return { candles: [], markerDate: baseDate };

    const candles = sliceByRange(all, range);
    // baseDate(휴장이면 이후 첫 거래일) 위치. 구간 밖이면 첫 봉으로 클램프.
    let baseIdx = candles.findIndex((c) => c.dt >= baseDate);
    if (baseIdx === -1) baseIdx = candles.length - 1;
    return { candles, markerDate: candles[baseIdx].dt };
  } catch (err) {
    console.error(`${code} 차트 조회 에러:`, err);
    return null;
  }
}

export default async function ChartTab({
  code,
  baseDate,
  name,
  range,
}: {
  code: string;
  baseDate: string;
  name?: string;
  range: ChartRange;
}) {
  const chart = await fetchChart(code, baseDate, range);
  const candles = chart?.candles ?? [];
  const rangeStart = candles.length > 0 ? candles[0].dt : null;
  const rangeEnd = candles.length > 0 ? candles[candles.length - 1].dt : null;

  return (
    <div>
      <div className="flex items-center gap-3 flex-wrap mb-6">
        <ChartRangePicker active={range} />
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

      {candles.length > 0 ? (
        <CandleChart data={candles} baseDate={chart!.markerDate} />
      ) : (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          데이터를 불러올 수 없습니다
        </div>
      )}
    </div>
  );
}
