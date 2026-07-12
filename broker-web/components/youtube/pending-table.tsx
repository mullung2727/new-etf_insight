"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import type { YoutubePendingItem } from "@/lib/queries";

function formatChars(n: number | null | undefined): string {
  if (n == null || n <= 0) return "—";
  return `${n.toLocaleString()}자`;
}

function isReady(p: YoutubePendingItem): boolean {
  return (
    p.status !== "no_transcript" &&
    p.has_transcript !== false &&
    (p.transcript_chars ?? 0) > 0
  );
}

function statusLabel(p: YoutubePendingItem): { text: string; className: string } {
  if (!isReady(p)) {
    return {
      text: "자막없음",
      className: "border-white/20 text-muted-foreground",
    };
  }
  return {
    text: "미요약",
    className: "border-fin-gold/40 text-fin-gold",
  };
}

type JobStatus = {
  job_id: string;
  status: string;
  channel_id?: string;
  video_id?: string;
  title?: string | null;
  message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string;
  detail?: string;
};

function errMsg(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const d = data as { detail?: unknown; error?: unknown; message?: unknown };
    if (typeof d.detail === "string") return d.detail;
    if (typeof d.error === "string") return d.error;
    if (typeof d.message === "string") return d.message;
    if (d.detail != null) return JSON.stringify(d.detail);
  }
  return `요청 실패 ${status}`;
}

function resultHref(from: string, to: string, channels: string[]): string {
  const q = new URLSearchParams({ tab: "result", from, to });
  for (const c of channels) q.append("channel", c);
  return `/youtube?${q.toString()}`;
}

function expandRange(
  from: string,
  to: string,
  dateKst: string | null | undefined,
): { from: string; to: string } {
  if (!dateKst || !/^\d{4}-\d{2}-\d{2}$/.test(dateKst)) return { from, to };
  return {
    from: dateKst < from ? dateKst : from,
    to: dateKst > to ? dateKst : to,
  };
}

export function YoutubePendingTable({
  items,
  filterFrom,
  filterTo,
  channelIds = [],
}: {
  items: YoutubePendingItem[];
  filterFrom: string;
  filterTo: string;
  channelIds?: string[];
}) {
  const router = useRouter();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  /** 요약 대상 date_kst — 완료 후 요약완료 필터에 포함 */
  const [jobDateKst, setJobDateKst] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showNoSub, setShowNoSub] = useState(false);

  const readyCount = useMemo(() => items.filter(isReady).length, [items]);
  const noSubCount = items.length - readyCount;
  const visible = useMemo(
    () => (showNoSub ? items : items.filter(isReady)),
    [items, showNoSub],
  );

  const selected = visible.find(
    (v) => `${v.channel_id}-${v.video_id}` === selectedKey,
  );
  const canSummarize = !!selected && isReady(selected);
  const jobRunning = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    if (!selectedKey) return;
    if (!visible.some((v) => `${v.channel_id}-${v.video_id}` === selectedKey)) {
      setSelectedKey(null);
    }
  }, [visible, selectedKey]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return;
    const id = job.job_id;
    const t = setInterval(async () => {
      try {
        const res = await fetch(`/api/youtube-summarize-job/${encodeURIComponent(id)}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setErr(errMsg(data, res.status));
          return;
        }
        const st = data as JobStatus;
        setJob(st);
        if (st.status === "done") {
          // 완료 후에만 요약완료 탭 이동 + 영상 날짜가 조회 기간 안 보이도록 확장
          const range = expandRange(filterFrom, filterTo, jobDateKst);
          router.push(resultHref(range.from, range.to, channelIds));
          router.refresh();
        } else if (st.status === "error") {
          setErr(st.message || st.error || "요약 실패");
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
    }, 2500);
    return () => clearInterval(t);
  }, [
    job?.job_id,
    job?.status,
    router,
    filterFrom,
    filterTo,
    channelIds,
    jobDateKst,
  ]);

  async function startSummarize() {
    if (!selected || !canSummarize) return;
    setBusy(true);
    setErr(null);
    setJobDateKst(selected.date_kst || null);
    try {
      const res = await fetch("/api/youtube-summarize-job", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_id: selected.channel_id,
          video_id: selected.video_id,
          title: selected.title || selected.video_id,
          force: false,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errMsg(data, res.status));
      setJob(data as JobStatus);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        대기 영상이 없습니다. 상단 「최근 수집」으로 영상을 가져온 뒤 여기에 나타납니다.
      </p>
    );
  }

  if (visible.length === 0) {
    return (
      <div className="space-y-2 text-sm text-muted-foreground">
        <p>
          요약 가능한 대기 영상이 없습니다.
          {noSubCount > 0 ? ` (자막없음 ${noSubCount}건 숨김)` : ""}
        </p>
        {noSubCount > 0 && (
          <label className="flex cursor-pointer items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={showNoSub}
              onChange={(e) => setShowNoSub(e.target.checked)}
              className="size-3.5 accent-[var(--fin-gold)]"
            />
            자막없음도 보기
          </label>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || jobRunning || !canSummarize}
          onClick={startSummarize}
        >
          {busy || jobRunning ? "요약 진행 중…" : "요약"}
        </Button>
        <span className="text-xs text-muted-foreground">
          미요약 {readyCount}건
          {noSubCount > 0 ? ` · 자막없음 ${noSubCount}` : ""}
          {canSummarize ? " · 선택됨" : " · 행 클릭으로 선택"}
        </span>
        {noSubCount > 0 && (
          <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showNoSub}
              onChange={(e) => setShowNoSub(e.target.checked)}
              className="size-3.5 accent-[var(--fin-gold)]"
            />
            자막없음도 보기
          </label>
        )}
      </div>

      {job && (
        <div className="rounded-md border border-border px-3 py-2 text-xs space-y-1">
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <span>
              잡{" "}
              <span className="font-mono text-fin-gold">{job.job_id}</span>
            </span>
            <span>
              상태{" "}
              <b
                className={
                  job.status === "done"
                    ? "text-fin-gold"
                    : job.status === "error"
                      ? "text-destructive"
                      : "text-foreground"
                }
              >
                {job.status}
              </b>
            </span>
            {job.video_id && (
              <span className="font-mono text-muted-foreground">{job.video_id}</span>
            )}
          </div>
          {job.title && (
            <p className="text-muted-foreground truncate" title={job.title}>
              {job.title}
            </p>
          )}
          {job.message && <p className="text-foreground/80">{job.message}</p>}
          {jobRunning && (
            <p className="text-muted-foreground">완료되면 요약완료 탭으로 이동합니다.</p>
          )}
        </div>
      )}
      {err && <p className="text-xs text-destructive break-all">{err}</p>}

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">날짜</th>
              <th className="px-3 py-2 font-medium">상태</th>
              <th className="px-3 py-2 font-medium">채널</th>
              <th className="px-3 py-2 font-medium">제목</th>
              <th className="px-3 py-2 font-medium">대본</th>
              <th className="px-3 py-2 font-medium">링크</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((v) => {
              const st = statusLabel(v);
              const key = `${v.channel_id}-${v.video_id}`;
              const checked = selectedKey === key;
              const selectable = isReady(v);
              return (
                <tr
                  key={key}
                  className={
                    "border-b border-border/60 " +
                    (checked ? "bg-fin-gold/5" : "") +
                    (selectable ? " cursor-pointer hover:bg-muted/30" : " opacity-60")
                  }
                  onClick={() => {
                    if (selectable) setSelectedKey(key);
                  }}
                >
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground whitespace-nowrap">
                    {v.date_kst}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={
                        "inline-flex rounded border px-1.5 py-0.5 text-[11px] " +
                        st.className
                      }
                    >
                      {st.text}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {v.channel_label || v.channel_id}
                  </td>
                  <td className="px-3 py-2 font-medium">{v.title || v.video_id}</td>
                  <td className="px-3 py-2 font-mono text-xs tabular-nums">
                    {formatChars(v.transcript_chars)}
                  </td>
                  <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    <a
                      href={v.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex rounded border border-fin-gold/50 bg-fin-gold/10 px-2 py-1 text-xs text-fin-gold hover:underline"
                    >
                      열기
                    </a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-muted-foreground">
        미요약 행 클릭 → 요약. 백그라운드 1잡 · 완료 후 요약완료 탭으로 이동.
      </p>
    </div>
  );
}
