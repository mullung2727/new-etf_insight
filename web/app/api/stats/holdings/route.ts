import { NextRequest, NextResponse } from "next/server";
import { getCountries, getHoldingsStats, getStatsSummary } from "@/lib/queries";

export async function GET(req: NextRequest) {
  try {
    const s = req.nextUrl.searchParams;
    const p = {
      begin: s.get("begin") ?? undefined,
      end: s.get("end") ?? undefined,
      country: s.get("country") ?? undefined,
    };
    const [stats, summary, countries] = await Promise.all([
      getHoldingsStats(p),
      getStatsSummary(p),
      getCountries(),
    ]);
    return NextResponse.json({ stats, summary, countries });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
