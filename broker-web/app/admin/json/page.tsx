"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type FileMeta = {
  key: string;
  label: string;
  relPath: string;
  description: string;
  risk: "safe" | "caution";
};

export default function JsonAdminPage() {
  const [files, setFiles] = useState<FileMeta[]>([]);
  const [selected, setSelected] = useState<FileMeta | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/json-files")
      .then((r) => r.json())
      .then(setFiles)
      .catch(() => setFiles([]));
  }, []);

  const load = useCallback(async (f: FileMeta) => {
    setSelected(f);
    setParseError(null);
    setSaveMsg(null);
    setContent("불러오는 중…");
    const res = await fetch(`/api/json-files?key=${f.key}`);
    const body = await res.json();
    setContent(body.content ?? "");
    setOriginal(body.content ?? "");
  }, []);

  const onChange = (v: string) => {
    setContent(v);
    setSaveMsg(null);
    try {
      JSON.parse(v);
      setParseError(null);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "JSON 파싱 실패");
    }
  };

  const save = async () => {
    if (!selected || parseError) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const res = await fetch("/api/json-files", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: selected.key, content }),
      });
      const body = await res.json();
      if (!res.ok) {
        setSaveMsg(`저장 실패: ${body.error ?? res.status}`);
      } else {
        setOriginal(content);
        setSaveMsg("저장됨 (.bak 백업 생성)");
      }
    } catch (e) {
      setSaveMsg(`저장 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setSaving(false);
    }
  };

  const dirty = content !== original;

  return (
    <div className="flex gap-6 p-6">
      {/* 파일 목록 */}
      <aside className="w-64 shrink-0 space-y-1">
        <h1 className="mb-3 text-sm font-semibold">관리 JSON 파일</h1>
        {files.map((f) => (
          <button
            key={f.key}
            onClick={() => load(f)}
            className={cn(
              "flex w-full flex-col gap-1 rounded-md px-3 py-2 text-left text-sm transition-colors",
              selected?.key === f.key
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground"
            )}
          >
            <span className="flex items-center gap-2">
              {f.label}
              {f.risk === "caution" && (
                <Badge variant="destructive" className="text-[10px]">
                  주의
                </Badge>
              )}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">{f.relPath}</span>
          </button>
        ))}
      </aside>

      {/* 편집 패널 */}
      <section className="min-w-0 flex-1">
        {!selected ? (
          <p className="text-sm text-muted-foreground">편집할 파일을 선택하세요.</p>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold">{selected.label}</h2>
                {selected.risk === "caution" && (
                  <Badge variant="destructive">주의</Badge>
                )}
              </div>
              <p className="font-mono text-xs text-muted-foreground">{selected.relPath}</p>
              <p className="text-sm text-muted-foreground">{selected.description}</p>
              {selected.risk === "caution" && (
                <p className="text-sm text-destructive">
                  ⚠ 배치 스케줄 파일입니다. 잘못 저장하면 배치가 실패할 수 있습니다.
                </p>
              )}
            </div>

            <textarea
              value={content}
              onChange={(e) => onChange(e.target.value)}
              spellCheck={false}
              className={cn(
                "h-[60vh] w-full resize-y rounded-md border bg-background p-3 font-mono text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                parseError ? "border-destructive" : "border-border"
              )}
            />

            <div className="flex items-center gap-3">
              <Button onClick={save} disabled={saving || !!parseError || !dirty}>
                {saving ? "저장 중…" : "저장"}
              </Button>
              {parseError && (
                <span className="text-sm text-destructive">JSON 오류: {parseError}</span>
              )}
              {!parseError && saveMsg && (
                <span className="text-sm text-muted-foreground">{saveMsg}</span>
              )}
              {!parseError && !saveMsg && dirty && (
                <span className="text-sm text-muted-foreground">변경됨 — 저장 필요</span>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
