import { NextRequest, NextResponse } from "next/server";
import { fetchFinancial } from "@/lib/dart";
import { FsDivType, ReprtCode } from "@/types/dart";

export async function GET(req: NextRequest) {
  try {
    const params = req.nextUrl.searchParams;
    const corpCode = params.get("corp_code");
    const year = params.get("year");
    const reprt = params.get("reprt") as ReprtCode | null;
    const fsDiv = params.get("fs_div") as FsDivType | null;

    if (!corpCode || !year || !reprt || !fsDiv) {
      return NextResponse.json(
        { error: "corp_code, year, reprt, fs_div 파라미터가 모두 필요합니다." },
        { status: 400 }
      );
    }

    const validReprts: ReprtCode[] = ["11011", "11012", "11013", "11014"];
    if (!validReprts.includes(reprt)) {
      return NextResponse.json({ error: "유효하지 않은 reprt 값입니다." }, { status: 400 });
    }

    if (fsDiv !== "CFS" && fsDiv !== "OFS") {
      return NextResponse.json({ error: "fs_div는 CFS 또는 OFS이어야 합니다." }, { status: 400 });
    }

    const data = await fetchFinancial(corpCode, year, reprt, fsDiv);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "서버 오류" },
      { status: 500 }
    );
  }
}
