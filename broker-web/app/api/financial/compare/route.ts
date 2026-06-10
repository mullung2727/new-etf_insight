import { NextRequest, NextResponse } from "next/server";
import { fetchCompare } from "@/lib/dart";

export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams;
  const corpCode = params.get("corp_code");
  const yearsParam = params.get("years");

  if (!corpCode) {
    return NextResponse.json({ error: "corp_code 파라미터가 필요합니다." }, { status: 400 });
  }

  const years = yearsParam ? parseInt(yearsParam, 10) : 5;
  if (isNaN(years) || years < 1 || years > 10) {
    return NextResponse.json({ error: "years는 1~10 사이 정수여야 합니다." }, { status: 400 });
  }

  try {
    const data = await fetchCompare(corpCode, years);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "서버 오류" },
      { status: 500 }
    );
  }
}
