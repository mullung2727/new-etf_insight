import { NextRequest, NextResponse } from "next/server";
import { getCountries, getEtfList } from "@/lib/queries";

export async function GET(req: NextRequest) {
  try {
    const s = req.nextUrl.searchParams;
    const [etfs, countries] = await Promise.all([
      getEtfList({
        begin: s.get("begin") ?? undefined,
        end: s.get("end") ?? undefined,
        country: s.get("country") ?? undefined,
        themeStatus: s.get("theme_status") ?? undefined,
        themeBucket: s.get("theme_bucket") ?? undefined,
        preListingOnly: s.get("pre_listing") === "true" ? true : undefined,
      }),
      getCountries(),
    ]);
    return NextResponse.json({ etfs, countries });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
