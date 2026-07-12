import { NextRequest, NextResponse } from "next/server";

const BASE = process.env.FASTAPI_BASE_URL ?? "http://localhost:8000";

/** 선택 채널 RSS 목록 + duration (대본/STT 없음) */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${BASE}/youtube/catalog`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(300_000),
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
      { error: err instanceof Error ? err.message : "catalog proxy failed" },
      { status: 502 },
    );
  }
}
