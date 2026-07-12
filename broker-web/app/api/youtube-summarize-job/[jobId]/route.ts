import { NextRequest, NextResponse } from "next/server";

const BASE = process.env.FASTAPI_BASE_URL ?? "http://localhost:8000";

/** 요약 잡 상태 조회 */
export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ jobId: string }> },
) {
  try {
    const { jobId } = await ctx.params;
    const res = await fetch(`${BASE}/youtube/summarize-job/${encodeURIComponent(jobId)}`, {
      signal: AbortSignal.timeout(15_000),
    });
    const text = await res.text();
    let data: unknown = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { error: text || `upstream ${res.status}` };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "job status proxy failed" },
      { status: 502 },
    );
  }
}
