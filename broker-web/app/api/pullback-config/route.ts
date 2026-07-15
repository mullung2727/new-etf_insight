import { NextRequest, NextResponse } from "next/server";
import { read, write, type PullbackConfig } from "@/lib/pullback-config";

function fail(error: unknown) {
  return NextResponse.json(
    { error: error instanceof Error ? error.message : "오류" },
    { status: 400 }
  );
}

export async function GET() {
  try {
    return NextResponse.json(await read());
  } catch (error) {
    return fail(error);
  }
}

export async function PUT(request: NextRequest) {
  try {
    const config = (await request.json()) as PullbackConfig;
    await write(config);
    return NextResponse.json(await read());
  } catch (error) {
    return fail(error);
  }
}
