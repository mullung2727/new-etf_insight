import { NextRequest, NextResponse } from "next/server";
import {
  listChannels,
  addChannel,
  editChannel,
  softDelete,
  restoreChannel,
} from "@/lib/telegram-channels";

function fail(err: unknown) {
  return NextResponse.json(
    { error: err instanceof Error ? err.message : "오류" },
    { status: 400 }
  );
}

// GET → { active, deleted }
export async function GET() {
  return NextResponse.json(await listChannels());
}

// POST { action:"add", url, label?, discovery } | { action:"restore", key }
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (body.action === "restore") {
      await restoreChannel(body.key);
    } else {
      await addChannel({ url: body.url, label: body.label, discovery: !!body.discovery });
    }
    return NextResponse.json(await listChannels());
  } catch (err) {
    return fail(err);
  }
}

// PUT { key, label?, discovery } → 수정
export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    await editChannel({ key: body.key, label: body.label, discovery: !!body.discovery });
    return NextResponse.json(await listChannels());
  } catch (err) {
    return fail(err);
  }
}

// DELETE { key } → 소프트delete(사이드카 이동)
export async function DELETE(req: NextRequest) {
  try {
    const body = await req.json();
    await softDelete(body.key);
    return NextResponse.json(await listChannels());
  } catch (err) {
    return fail(err);
  }
}
