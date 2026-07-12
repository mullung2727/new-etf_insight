"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";

export type CatalogItem = {
  channel_id: string;
  channel_label?: string | null;
  video_id: string;
  title: string;
  published_at_utc: string;
  date_kst: string;
  url: string;
  duration_sec: number | null;
};

function rowKey(v: CatalogItem): string {
  return `${v.channel_id}:${v.video_id}`;
}

function formatDuration(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return "—";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  }
  return `${m}:${String(r).padStart(2, "0")}`;
}

function durationHint(sec: number | null | undefined): string {
  if (sec == null) return "";
  if (sec < 600) return "짧음";
  if (sec < 1200) return "중간";
  return "김";
}

function errMsg(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const d = data as { detail?: unknown; error?: unknown };
    if (typeof d.detail === "string") return d.detail;
    if (typeof d.error === "string") return d.error;
    if (d.detail != null) return JSON.stringify(d.detail);
  }
  if (typeof data === "string" && data) return data;
  return `실패 ${status}`;
}

export function YoutubeCatalogPanel({ channelIds }: { channelIds: string[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"list" | "collect" | null>(null);
  const [items, setItems] = useState<CatalogItem[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [sortDur, setSortDur] = useState(false);

  const view = useMemo(() => {
    if (!items) return null;
    const rows = [...items];
    if (sortDur) {
      rows.sort((a, b) => {
        const da = a.duration_sec ?? -1;
        const db = b.duration_sec ?? -1;
        return da - db;
      });
    }
    return rows;
  }, [items, sortDur]);

  function toggle(key: string) {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  }

  function toggleAll(rows: CatalogItem[]) {
    setSelected((prev) => {
      const keys = rows.map(rowKey);
      const allOn = keys.length > 0 && keys.every((k) => prev.has(k));
      if (allOn) return new Set();
      return new Set(keys);
    });
  }

  async function load() {
    if (channelIds.length === 0) {
      setErr("채널을 하나 이상 선택하세요.");
      return;
    }
    setBusy("list");
    setErr(null);
    setMsg(null);
    try {
      const res = await fetch("/api/youtube-catalog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_ids: channelIds, with_duration: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errMsg(data, res.status));
      if (!Array.isArray(data)) throw new Error("목록 형식 오류");
      setItems(data as CatalogItem[]);
      setSelected(new Set());
    } catch (e) {
      setItems(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function collectSelected() {
    if (!items || selected.size === 0) {
      setErr("수집할 영상을 선택하세요.");
      return;
    }
    const videos = items
      .filter((v) => selected.has(rowKey(v)))
      .map((v) => ({
        channel_id: v.channel_id,
        video_id: v.video_id,
        title: v.title,
        published_at_utc: v.published_at_utc,
        date_kst: v.date_kst,
        url: v.url,
      }));
    setBusy("collect");
    setErr(null);
    setMsg(null);
    try {
      const res = await fetch("/api/youtube-collect-selected", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videos }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errMsg(data, res.status));
      const r = data as {
        targets?: number;
        matched?: number;
        inserted?: number;
        updated?: number;
        skipped_no_transcript?: number;
        error?: string;
      };
      if (r.error) throw new Error(r.error);
      setMsg(
        `선택 수집 완료: 대상 ${r.targets ?? videos.length} · 저장 ${r.matched ?? 0}` +
          ` (신규 ${r.inserted ?? 0} · 갱신 ${r.updated ?? 0})` +
          (r.skipped_no_transcript
            ? ` · 자막없음 ${r.skipped_no_transcript}`
            : "") +
          " · 유튜브 자막 API (STT 아님)",
      );
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const allSelected =
    !!view && view.length > 0 && view.every((v) => selected.has(rowKey(v)));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!!busy || channelIds.length === 0}
          onClick={load}
        >
          {busy === "list" ? "불러오는 중…" : "영상 목록 불러오기"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!!busy || selected.size === 0}
          onClick={collectSelected}
        >
          {busy === "collect"
            ? "수집 중…"
            : `선택 수집${selected.size ? ` (${selected.size})` : ""}`}
        </Button>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={sortDur}
            onChange={(e) => setSortDur(e.target.checked)}
            className="size-3.5 accent-[var(--fin-gold)]"
          />
          짧은 순 정렬
        </label>
        <span className="text-xs text-muted-foreground">
          선택 채널 {channelIds.length}개 · 자막 API 수동 수집 · STT 없음
        </span>
      </div>

      {channelIds.length === 0 && (
        <p className="text-sm text-muted-foreground">
          위 필터에서 채널을 체크한 뒤 목록을 불러오세요. 짧은 영상만 골라 수집하면 됩니다.
        </p>
      )}
      {msg && <p className="text-xs text-fin-gold">{msg}</p>}
      {err && <p className="text-sm text-destructive break-all">{err}</p>}

      {view && view.length === 0 && (
        <p className="text-sm text-muted-foreground">영상이 없습니다.</p>
      )}

      {view && view.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/30 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium w-8">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={() => toggleAll(view)}
                    className="size-3.5 accent-[var(--fin-gold)]"
                    aria-label="전체 선택"
                  />
                </th>
                <th className="px-3 py-2 font-medium">채널</th>
                <th className="px-3 py-2 font-medium">제목</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">날짜</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">길이</th>
                <th className="px-3 py-2 font-medium">비고</th>
              </tr>
            </thead>
            <tbody>
              {view.map((v) => {
                const key = rowKey(v);
                const hint = durationHint(v.duration_sec);
                const checked = selected.has(key);
                return (
                  <tr
                    key={key}
                    className={
                      "border-b border-border/60 last:border-0 " +
                      (checked ? "bg-fin-gold/5" : "")
                    }
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(key)}
                        className="size-3.5 accent-[var(--fin-gold)]"
                        aria-label={`${v.title || v.video_id} 선택`}
                      />
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground max-w-[8rem] truncate">
                      {v.channel_label || v.channel_id}
                    </td>
                    <td className="px-3 py-2">
                      <a
                        href={v.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-fin-gold hover:underline"
                        title={v.title}
                      >
                        {v.title || v.video_id}
                      </a>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">
                      {v.date_kst || "—"}
                    </td>
                    <td
                      className={
                        "px-3 py-2 font-mono text-xs whitespace-nowrap " +
                        (v.duration_sec != null && v.duration_sec >= 1200
                          ? "text-destructive"
                          : v.duration_sec != null && v.duration_sec < 600
                            ? "text-fin-gold"
                            : "")
                      }
                    >
                      {formatDuration(v.duration_sec)}
                      {v.duration_sec != null ? (
                        <span className="ml-1 text-muted-foreground">
                          ({v.duration_sec}s)
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {hint}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="px-3 py-2 text-[11px] text-muted-foreground border-t border-border">
            {view.length}건 · 체크 후 「선택 수집」= 유튜브 자막만 (STT 아님) · 10분
            미만=짧음, 20분 이상=김 (표시만)
          </p>
        </div>
      )}
    </div>
  );
}
