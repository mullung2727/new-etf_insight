import { Suspense } from "react";
import { getEtfList, getCountries } from "@/lib/queries";
import { FilterBar } from "@/components/etf/filter-bar";
import { EtfTable } from "@/components/etf/etf-table";

function defaultBegin(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

interface PageProps {
  searchParams: Promise<{ begin?: string; end?: string; country?: string; theme_status?: string; theme_bucket?: string }>;
}

export default async function EtfsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const [etfs, countries] = await Promise.all([
    getEtfList({
      begin: params.begin ?? defaultBegin(),
      end: params.end,
      country: params.country,
      themeStatus: params.theme_status,
      themeBucket: params.theme_bucket,
    }),
    getCountries(),
  ]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">신규 상장예정 ETF</h1>
        <p className="text-sm text-muted-foreground mt-1">최초 공시일 기준으로 필터링합니다.</p>
      </div>
      <Suspense>
        <FilterBar countries={countries} />
      </Suspense>
      <div className="rounded-md border border-border">
        <EtfTable etfs={etfs} />
      </div>
      <p className="text-xs text-muted-foreground">총 {etfs.length}건</p>
    </div>
  );
}
