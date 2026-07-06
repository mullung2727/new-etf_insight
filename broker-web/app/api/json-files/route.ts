import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs/promises";
import { MANAGED_FILES, findManaged, resolveManaged } from "@/lib/json-admin";

// GET            → 관리 파일 목록(메타만)
// GET ?key=xxx   → 해당 파일 원문 + 메타
export async function GET(req: NextRequest) {
  const key = req.nextUrl.searchParams.get("key");
  if (!key) {
    return NextResponse.json(
      MANAGED_FILES.map(({ key, label, relPath, description, risk }) => ({
        key,
        label,
        relPath,
        description,
        risk,
      }))
    );
  }
  const meta = findManaged(key);
  if (!meta) {
    return NextResponse.json({ error: `알 수 없는 파일 key: ${key}` }, { status: 400 });
  }
  try {
    const { absPath } = resolveManaged(key);
    const content = await fs.readFile(absPath, "utf-8");
    return NextResponse.json({
      key: meta.key,
      label: meta.label,
      relPath: meta.relPath,
      description: meta.description,
      risk: meta.risk,
      content,
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "읽기 오류" },
      { status: 500 }
    );
  }
}

// PUT {key, content} → 검증 후 저장(.bak 백업 → 원문 그대로 기록)
export async function PUT(req: NextRequest) {
  let body: { key?: string; content?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "잘못된 요청 본문" }, { status: 400 });
  }
  const { key, content } = body;
  if (!key || typeof content !== "string") {
    return NextResponse.json({ error: "key와 content가 필요합니다." }, { status: 400 });
  }
  if (!findManaged(key)) {
    return NextResponse.json({ error: `알 수 없는 파일 key: ${key}` }, { status: 400 });
  }
  // 저장 전 JSON 유효성 검증 — 깨진 파일 유입 차단
  try {
    JSON.parse(content);
  } catch (err) {
    return NextResponse.json(
      { error: `JSON 파싱 실패: ${err instanceof Error ? err.message : err}` },
      { status: 400 }
    );
  }
  try {
    const { absPath } = resolveManaged(key);
    // 직전 버전 백업(.bak 1개, 롤백용)
    const prev = await fs.readFile(absPath, "utf-8").catch(() => null);
    if (prev !== null) await fs.writeFile(absPath + ".bak", prev, "utf-8");
    await fs.writeFile(absPath, content, "utf-8");
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "저장 오류" },
      { status: 500 }
    );
  }
}
