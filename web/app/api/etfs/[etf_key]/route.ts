import { NextRequest, NextResponse } from "next/server";
import { getEtfDetail, getEtfHoldings } from "@/lib/queries";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ etf_key: string }> }
) {
  try {
    const { etf_key } = await params;
    const [detail, holdings] = await Promise.all([
      getEtfDetail(etf_key),
      getEtfHoldings(etf_key),
    ]);
    if (!detail) {
      return NextResponse.json({ error: "not found" }, { status: 404 });
    }
    return NextResponse.json({ detail, holdings });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
