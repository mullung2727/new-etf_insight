"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";

/** same-origin 프록시 (브라우저→:3000/api → api:8000). */

type CollectResult = {
  channels?: number;
  results?: { matched_date?: number; inserted?: number }[];
  errors?: unknown[];
  error?: string;
};

type CollectUrlResult = {
  video_id: string;
  channel_id: string;
  date_kst: string;
  title: string;
  status: string;
  has_transcript?: boolean;
  url?: string;
  error?: string;
  detail?: string;
};

type JobStatus = {
  job_id: string;
  status: string;
  title?: string | null;
  message?: string | null;
};

function kstYmd(d: Date = new Date()): string {
  const t = new Date(d.getTime() + 9 * 60 * 60 * 1000);
  return t.toISOString().slice(0, 10);
}

function kstDaysAgo(n: number): string {
  const d = new Date();
  d.setTime(d.getTime() - n * 24 * 60 * 60 * 1000);
  return kstYmd(d);
}

function apiErr(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const d = data as { detail?: unknown; error?: unknown; message?: unknown };
    if (typeof d.detail === "string") return d.detail;
    if (typeof d.error === "string") return d.error;
    if (typeof d.message === "string") return d.message;
    if (d.detail != null) return JSON.stringify(d.detail);
  }
  return `요청 실패 ${status}`;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as T;
  if (!res.ok) {
    throw new Error(apiErr(data, res.status));
  }
  if (data && typeof data === "object" && "error" in data) {
    const e = (data as { error?: string }).error;
    if (e) throw new Error(e);
  }
  return data;
}

function expandRange(
  from: string,
  to: string,
  dateKst: string,
): { from: string; to: string } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateKst)) return { from, to };
  return {
    from: dateKst < from ? dateKst : from,
    to: dateKst > to ? dateKst : to,
  };
}

function youtubeHref(
  tab: "pending" | "result",
  from: string,
  to: string,
  channelIds: string[],
): string {
  const q = new URLSearchParams({ tab, from, to });
  for (const c of channelIds) q.append("channel", c);
  return `/youtube?${q.toString()}`;
}

export function YoutubeOpsBar({
  filterFrom,
  filterTo,
  channelIds,
}: {
  /** 조회 필터 (표 전용). 수집 기본값과 분리. */
  filterFrom: string;
  filterTo: string;
  channelIds: string[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"collect" | "url" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [job, setJob] = useState<JobStatus | null>(null);
  /** 요약 대상 date_kst — 완료 후 요약완료 필터에 포함 */
  const [jobDateKst, setJobDateKst] = useState<string | null>(null);
  /** 고급: 조회 필터 기간으로 수집 */
  const [useFilterRange, setUseFilterRange] = useState(false);

  const defaultCollect = useMemo(
    () => ({ from: kstDaysAgo(1), to: kstYmd() }),
    [],
  );

  const jobRunning = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return;
    const id = job.job_id;
    const t = setInterval(async () => {
      try {
        const res = await fetch(
          `/api/youtube-summarize-job/${encodeURIComponent(id)}`,
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setErr(apiErr(data, res.status));
          return;
        }
        const st = data as JobStatus;
        setJob(st);
        if (st.status === "done") {
          const range = expandRange(filterFrom, filterTo, jobDateKst ?? "");
          router.push(youtubeHref("result", range.from, range.to, channelIds));
          router.refresh();
        } else if (st.status === "error") {
          setErr(st.message || "요약 실패");
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
    }, 2500);
    return () => clearInterval(t);
  }, [job, router, filterFrom, filterTo, channelIds, jobDateKst]);

  const collectFrom = useFilterRange ? filterFrom : defaultCollect.from;
  const collectTo = useFilterRange ? filterTo : defaultCollect.to;
  const channels = channelIds.length > 0 ? channelIds : undefined;

  async function onCollect() {
    setBusy("collect");
    setMsg(null);
    setErr(null);
    try {
      const r = await postJson<CollectResult>("/api/youtube-collect", {
        from_date: collectFrom,
        to_date: collectTo,
        channel_ids: channels,
      });
      const matched = (r.results || []).reduce(
        (s, x) => s + (x.matched_date || 0),
        0,
      );
      const ins = (r.results || []).reduce((s, x) => s + (x.inserted || 0), 0);
      setMsg(
        `수집 완료 (${collectFrom}~${collectTo}): 채널 ${r.channels ?? 0} · 매칭 ${matched} · 신규 ${ins}` +
          (r.errors?.length ? ` · 실패 ${r.errors.length}` : "") +
          " · RSS 최근분만",
      );
      router.push(youtubeHref("pending", filterFrom, filterTo, channelIds));
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function onCollectUrl() {
    const url = videoUrl.trim();
    if (!url) {
      setErr("영상 URL을 입력하세요.");
      return;
    }
    setBusy("url");
    setMsg(null);
    setErr(null);
    try {
      const r = await postJson<CollectUrlResult>("/api/youtube-collect-url", {
        url,
      });
      const range = expandRange(filterFrom, filterTo, r.date_kst);
      if (r.status === "already_summarized") {
        // 탭은 유지(사용자 선택). 조회 기간만 date_kst 포함하게 확장.
        setMsg(
          `이미 요약됨: ${r.title || r.video_id} (${r.date_kst}) — 요약완료 탭을 확인하세요.`,
        );
        const cur =
          typeof window !== "undefined" &&
          new URLSearchParams(window.location.search).get("tab") === "pending"
            ? "pending"
            : "result";
        router.push(youtubeHref(cur, range.from, range.to, channelIds));
        router.refresh();
      } else if (r.has_transcript === false) {
        // 자막없음 → 자동 요약 안 함(STT 비용). 대기 탭 「요약」으로 원할 때만 STT.
        setMsg(
          `가져오기 ${r.status}: ${r.title || r.video_id} (${r.date_kst}) · 자막없음 — 대기 탭에서 「요약」 누르면 STT로 진행`,
        );
        router.push(youtubeHref("pending", range.from, range.to, channelIds));
        router.refresh();
      } else {
        setJobDateKst(r.date_kst);
        const j = await postJson<JobStatus>("/api/youtube-summarize-job", {
          channel_id: r.channel_id,
          video_id: r.video_id,
          title: r.title || r.video_id,
          force: false,
        });
        setJob(j);
        setMsg(`요약 시작: ${r.title || r.video_id} (${r.date_kst})`);
      }
      setVideoUrl("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">작업</span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!!busy}
          onClick={onCollect}
        >
          {busy === "collect" ? "수집 중…" : "최근 수집"}
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="url"
          value={videoUrl}
          onChange={(e) => setVideoUrl(e.target.value)}
          placeholder="영상 URL (watch / shorts / youtu.be)"
          disabled={!!busy}
          className="h-8 min-w-[16rem] flex-1 rounded-md border border-border bg-background px-2 text-sm"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void onCollectUrl();
            }
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!!busy || jobRunning || !videoUrl.trim()}
          onClick={onCollectUrl}
        >
          {busy === "url"
            ? "가져오는 중…"
            : jobRunning
              ? "요약 진행 중…"
              : "주소로 요약"}
        </Button>
      </div>

      <p className="text-[11px] text-muted-foreground leading-relaxed">
        <b className="text-foreground/80">최근 수집</b> 기본:{" "}
        <span className="font-mono text-fin-gold">
          {collectFrom} ~ {collectTo}
        </span>
        {" "}
        (어제~오늘, RSS 최근 ~15 · 자막 API · STT 아님)
        {" · "}
        채널: {channels ? `${channels.length}개 선택` : "등록 전체"}
        <br />
        <b className="text-foreground/80">주소로 요약</b>: 영상 1건 적재 → 자막 있으면
        바로 요약(완료 시 요약완료 탭) · 이미 요약됨은 스킵 · 자막없으면 대기로만
        (STT는 대기 탭에서 수동)
        <br />
        <b className="text-foreground/80">요약</b>:{" "}
        <b>대기</b> 탭에서 미요약 행 클릭 → 「요약」 (완료 후 요약완료 탭)
      </p>

      <label className="flex cursor-pointer items-center gap-2 text-[11px] text-muted-foreground">
        <input
          type="checkbox"
          checked={useFilterRange}
          onChange={(e) => setUseFilterRange(e.target.checked)}
          className="size-3.5 accent-[var(--fin-gold)]"
        />
        <span>
          고급: 최근 수집도 조회 기간 사용 (
          <span className="font-mono">
            {filterFrom}~{filterTo}
          </span>
          )
        </span>
      </label>

      {job && (
        <p className="text-xs text-muted-foreground">
          잡 <span className="font-mono text-fin-gold">{job.job_id}</span> · 상태{" "}
          <b className={job.status === "error" ? "text-destructive" : "text-foreground"}>
            {job.status}
          </b>
          {job.message ? ` · ${job.message}` : ""}
        </p>
      )}
      {msg && <p className="text-xs text-fin-gold">{msg}</p>}
      {err && <p className="text-xs text-destructive break-all">{err}</p>}
    </div>
  );
}
