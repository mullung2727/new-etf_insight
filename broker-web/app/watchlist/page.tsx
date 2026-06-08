import stockData from "@/data/stock_data.json";
import { fetchCorpCodes } from "@/lib/dart";
import Link from "next/link";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

function formatDate(raw: string): string {
  return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}

export default async function WatchlistPage() {
  const corpCodes = await fetchCorpCodes();
  const nameMap = Object.fromEntries(
    corpCodes
      .filter((c) => c.stock_code)
      .map((c) => [c.stock_code, c.corp_name])
  );

  const entries = Object.entries(stockData as Record<string, string[]>).sort(
    ([a], [b]) => b.localeCompare(a)
  );

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      <main className="max-w-3xl mx-auto py-10 px-4">
        <h1 className="text-2xl font-bold mb-6 text-primary tracking-[0.05em]">
          일별 모니터링 종목
        </h1>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-36">날짜</TableHead>
              <TableHead>종목</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map(([date, codes]) => (
              <TableRow key={date}>
                <TableCell className="font-mono text-sm">
                  {formatDate(date)}
                </TableCell>
                <TableCell className="flex flex-wrap gap-1.5">
                  {codes.map((code) => (
                    <Link
                      key={code}
                      href={`/watchlist/${code}?date=${date}&name=${encodeURIComponent(nameMap[code] ?? code)}`}
                    >
                      <Badge variant="secondary" title={code} className="cursor-pointer hover:bg-secondary/80">
                        {nameMap[code] ?? code}
                      </Badge>
                    </Link>
                  ))}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </main>
    </div>
  );
}
