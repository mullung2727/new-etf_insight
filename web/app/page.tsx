import { Suspense } from "react";
import { getEtfList, getCountries } from "@/lib/queries";
import { FilterBar } from "@/components/filter-bar";
import { EtfTable } from "@/components/etf-table";

function defaultBegin(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

interface PageProps {
  searchParams: Promise<{ begin?: string; end?: string; country?: string }>;
}

export default async function Home({ searchParams }: PageProps) {
  const params = await searchParams;
  const begin = params.begin ?? defaultBegin();
  const end = params.end ?? undefined;
  const country = params.country ?? undefined;

  const [etfs, countries] = await Promise.all([
    getEtfList({ begin, end, country }),
    getCountries(),
  ]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">신규 상장예정 ETF</h1>
        <p className="text-sm text-muted-foreground mt-1">
          최초 공시일 기준으로 필터링합니다.
        </p>
      </div>
      <Suspense>
        <FilterBar countries={countries} />
      </Suspense>
      <div className="rounded-md border">
        <EtfTable etfs={etfs} />
      </div>
      <p className="text-xs text-muted-foreground">총 {etfs.length}건</p>
    </div>
  );
}
