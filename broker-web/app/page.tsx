import { apiGet } from "@/lib/api-client";
import { getLatestDigest, getRankings, getRankingPeriods, type DigestLatest, type RankingRow } from "@/lib/queries";
import { fetchCorpCodes } from "@/lib/dart";
import { DigestCard } from "@/components/home/digest-card";
import { WatchlistCard, type WatchlistCardItem } from "@/components/home/watchlist-card";
import { RoeCard } from "@/components/home/roe-card";
import { PositionSummary } from "@/components/home/position-summary";

export const dynamic = "force-dynamic";

// 한 소스가 죽어도 대시보드 전체가 아니라 해당 카드만 비게 한다.
async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try {
    return await p;
  } catch {
    return fallback;
  }
}

async function loadWatchlist(): Promise<{ date: string | null; items: WatchlistCardItem[] }> {
  const [wl, corps] = await Promise.all([
    safe(apiGet<Record<string, string[]>>("/watchlist"), {}),
    safe(fetchCorpCodes(), []),
  ]);
  const dates = Object.keys(wl).sort((a, b) => b.localeCompare(a));
  const date = dates[0] ?? null;
  if (!date) return { date: null, items: [] };
  const nameMap = Object.fromEntries(
    corps.filter((c) => c.stock_code).map((c) => [c.stock_code, c.corp_name])
  );
  const items = wl[date].map((code) => ({ code, name: nameMap[code] ?? code }));
  return { date, items };
}

async function loadRoe(): Promise<{ rows: RankingRow[]; periodLabel: string | null }> {
  const periods = await safe(getRankingPeriods(), []);
  const period = periods[0];
  if (!period) return { rows: [], periodLabel: null };
  const rows = await safe(
    getRankings({
      metric: "roe", year: period.year, reprt: period.reprt,
      excludeImpaired: true, limit: 5,
    }),
    []
  );
  return { rows, periodLabel: period.label };
}

export default async function Home() {
  const [digest, watchlist, roe] = await Promise.all([
    safe<DigestLatest | null>(getLatestDigest(), null),
    loadWatchlist(),
    loadRoe(),
  ]);

  return (
    <div className="max-w-5xl mx-auto flex flex-col gap-4 p-6">
      <PositionSummary />
      <DigestCard digest={digest} />
      <div className="grid gap-4 md:grid-cols-2">
        <WatchlistCard date={watchlist.date} items={watchlist.items} />
        <RoeCard rows={roe.rows} periodLabel={roe.periodLabel} />
      </div>
    </div>
  );
}
