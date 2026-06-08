import { NextResponse } from "next/server";
import { fetchCorpCodes } from "@/lib/dart";

export const revalidate = 86400;

export async function GET() {
  try {
    const data = await fetchCorpCodes();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "서버 오류" },
      { status: 500 }
    );
  }
}
