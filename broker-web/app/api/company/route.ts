import { NextRequest, NextResponse } from "next/server";
import { searchCompany } from "@/lib/dart";

export async function GET(req: NextRequest) {
  try {
    const corpCode = req.nextUrl.searchParams.get("corp_code");
    if (!corpCode) {
      return NextResponse.json({ error: "corp_code 파라미터가 필요합니다." }, { status: 400 });
    }
    const data = await searchCompany(corpCode);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "서버 오류" },
      { status: 500 }
    );
  }
}
