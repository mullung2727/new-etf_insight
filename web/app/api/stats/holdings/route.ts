import { NextRequest, NextResponse } from "next/server";
import {
  getCountries,
  getHoldingsStats,
  getHoldingsStatsByKeys,
  getStatsSummary,
  getStatsSummaryByKeys,
} from "@/lib/queries";

export async function GET(req: NextRequest) {
  try {
    const s = req.nextUrl.searchParams;
    const keysParam = s.get("keys");

    if (keysParam) {
      const keys = keysParam.split(",").map((k) => k.trim()).filter(Boolean);
      const [stats, summary] = await Promise.all([
        getHoldingsStatsByKeys(keys),
        getStatsSummaryByKeys(keys),
      ]);
      return NextResponse.json({ stats, summary });
    }

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
