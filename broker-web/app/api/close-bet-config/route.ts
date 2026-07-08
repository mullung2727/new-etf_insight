import { NextRequest, NextResponse } from "next/server";
import { read, write, type CloseBetConfig } from "@/lib/close-bet-config";

function fail(err: unknown) {
  return NextResponse.json(
    { error: err instanceof Error ? err.message : "오류" },
    { status: 400 }
  );
}

// GET → { score_threshold, tp, sl, budget_by_count }
export async function GET() {
  try {
    return NextResponse.json(await read());
  } catch (err) {
    return fail(err);
  }
}

// PUT { score_threshold, tp, sl, budget_by_count } → 검증 실패 시 400·파일 불변, 성공 시 .bak 후 저장
export async function PUT(req: NextRequest) {
  try {
    const body = (await req.json()) as CloseBetConfig;
    await write(body);
    return NextResponse.json(await read());
  } catch (err) {
    return fail(err);
  }
}
