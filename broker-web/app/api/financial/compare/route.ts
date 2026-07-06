import { NextRequest, NextResponse } from "next/server";
import { fetchCompare } from "@/lib/dart";

export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams;
  const corpCode = params.get("corp_code");

  if (!corpCode) {
    return NextResponse.json({ error: "corp_code 파라미터가 필요합니다." }, { status: 400 });
  }

  const mode = params.get("mode") === "quarterly" ? "quarterly" : "annual";
  // count: ?count 우선, 없으면 구 파라미터 ?years, 그래도 없으면 모드별 기본
  const countParam = params.get("count") ?? params.get("years");
  const count = countParam ? parseInt(countParam, 10) : (mode === "quarterly" ? 8 : 5);
  if (isNaN(count) || count < 1 || count > 12) {
    return NextResponse.json({ error: "기간 수는 1~12 사이 정수여야 합니다." }, { status: 400 });
  }

  try {
    const data = await fetchCompare(corpCode, count, mode);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "서버 오류" },
      { status: 500 }
    );
  }
}
