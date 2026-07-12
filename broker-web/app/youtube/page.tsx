import Link from "next/link";
import {
  getYoutubePending,
  getYoutubeSummaries,
  type YoutubePendingItem,
  type YoutubeVideoSummary,
} from "@/lib/queries";
import { stockHubHref } from "@/lib/stock-hub";
import { listChannels, type Channel } from "@/lib/youtube-channels";
import { YoutubeSummaryTable } from "@/components/youtube/summary-table";
import { YoutubePendingTable } from "@/components/youtube/pending-table";
import { YoutubeOpsBar } from "@/components/youtube/ops-bar";

function kstYmd(d: Date): string {
  const t = new Date(d.getTime() + 9 * 60 * 60 * 1000);
  return t.toISOString().slice(0, 10);
}

function defaultRange(): { from: string; to: string } {
  const now = new Date();
  const to = kstYmd(now);
  const fromDate = new Date(now.getTime());
  fromDate.setUTCDate(fromDate.getUTCDate() - 30);
  return { from: kstYmd(fromDate), to };
}

function parseChannelParam(raw: string | string[] | undefined): string[] {
  if (raw == null) return [];
  const parts = Array.isArray(raw) ? raw : [raw];
  const out: string[] = [];
  for (const p of parts) {
    for (const s of p.split(",")) {
      const t = s.trim();
      if (t && !out.includes(t)) out.push(t);
    }
  }
  return out;
}

async function loadActiveChannels(): Promise<Channel[]> {
  try {
    return (await listChannels()).active;
  } catch {
    return [];
  }
}

function withChannelNames(
  items: YoutubeVideoSummary[],
  channels: Channel[],
): YoutubeVideoSummary[] {
  const labels: Record<string, string> = {};
  for (const c of channels) labels[c.key] = c.label || c.handle || c.key;
  return items.map((v) => ({
    ...v,
    channel_label:
      (v.channel_label && v.channel_label.trim()) ||
      labels[v.channel_id] ||
      v.channel_id,
    title: (v.title && v.title.trim()) || v.video_id,
    url:
      (v.url && v.url.trim()) ||
      `https://www.youtube.com/watch?v=${v.video_id}`,
  }));
}

function filterByChannels<T extends { channel_id: string }>(
  items: T[],
  channelIds: string[],
): T[] {
  if (channelIds.length === 0) return items;
  const set = new Set(channelIds);
  return items.filter((v) => set.has(v.channel_id));
}

function tabHref(
  tab: "result" | "pending",
  from: string,
  to: string,
  channels: string[],
): string {
  const q = new URLSearchParams({ tab, from, to });
  for (const c of channels) q.append("channel", c);
  return `/youtube?${q.toString()}`;
}

export default async function YoutubePage({
  searchParams,
}: {
  searchParams: Promise<{
    from?: string;
    to?: string;
    channel?: string | string[];
    tab?: string;
  }>;
}) {
  const sp = await searchParams;
  const defaults = defaultRange();
  const from =
    sp.from && /^\d{4}-\d{2}-\d{2}$/.test(sp.from) ? sp.from : defaults.from;
  const to = sp.to && /^\d{4}-\d{2}-\d{2}$/.test(sp.to) ? sp.to : defaults.to;
  const selectedChannels = parseChannelParam(sp.channel);
  // list 탭 폐기. 알 수 없는 tab → 요약완료(result)
  const tab = sp.tab === "pending" ? "pending" : "result";

  let summaries: YoutubeVideoSummary[] | null = null;
  let pending: YoutubePendingItem[] | null = null;
  let allInRange: YoutubeVideoSummary[] = [];
  let error = false;
  let channels: Channel[] = [];

  try {
    const [rawSum, rawPend, ch] = await Promise.all([
      getYoutubeSummaries({ from, to }),
      getYoutubePending({
        from,
        to,
        channel: selectedChannels.length ? selectedChannels : undefined,
      }),
      loadActiveChannels(),
    ]);
    channels = ch;
    allInRange = withChannelNames(rawSum, channels);
    summaries = filterByChannels(allInRange, selectedChannels);
    pending = filterByChannels(
      rawPend.map((p) => ({
        ...p,
        channel_label:
          p.channel_label ||
          channels.find((c) => c.key === p.channel_id)?.label ||
          p.channel_id,
      })),
      selectedChannels,
    );
  } catch (err) {
    console.error("youtube page load error:", err);
    error = true;
  }

  const optionKeys = new Map<string, string>();
  for (const c of channels) {
    optionKeys.set(c.key, c.label || c.handle || c.key);
  }
  for (const v of allInRange) {
    if (!optionKeys.has(v.channel_id)) {
      optionKeys.set(v.channel_id, v.channel_label || v.channel_id);
    }
  }
  for (const p of pending || []) {
    if (!optionKeys.has(p.channel_id)) {
      optionKeys.set(p.channel_id, p.channel_label || p.channel_id);
    }
  }
  for (const id of selectedChannels) {
    if (!optionKeys.has(id)) optionKeys.set(id, id);
  }
  const channelOptions = [...optionKeys.entries()].map(([key, label]) => ({
    key,
    label,
  }));
  const filterAll = selectedChannels.length === 0;
  const pendingReady = (pending || []).filter(
    (p) =>
      p.status !== "no_transcript" &&
      p.has_transcript !== false &&
      (p.transcript_chars ?? 0) > 0,
  ).length;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">유튜브 영상 요약</h1>
          <p className="text-sm text-muted-foreground">
            가져오기 → 대기 → 요약 → 요약완료. 요약은 대기에서 1건씩.
          </p>
        </div>
        <Link href="/admin/settings" className="text-sm text-fin-gold hover:underline">
          채널 설정 →
        </Link>
      </div>

      <form
        className="space-y-3 rounded-md border border-border p-3"
        action="/youtube"
        method="get"
      >
        <input type="hidden" name="tab" value={tab} />
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="from" className="text-xs text-muted-foreground">
              시작일
            </label>
            <input
              id="from"
              name="from"
              type="date"
              defaultValue={from}
              className="h-8 rounded-md border border-border bg-background px-2 text-sm"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="to" className="text-xs text-muted-foreground">
              종료일
            </label>
            <input
              id="to"
              name="to"
              type="date"
              defaultValue={to}
              className="h-8 rounded-md border border-border bg-background px-2 text-sm"
            />
          </div>
          <button
            type="submit"
            className="h-8 rounded-md border border-border px-3 text-sm hover:bg-secondary"
          >
            조회
          </button>
          <Link
            href="/youtube"
            className="h-8 inline-flex items-center px-2 text-sm text-muted-foreground hover:text-foreground"
          >
            초기화
          </Link>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>채널 (조회 필터)</span>
            <span>
              {filterAll
                ? `(전체 ${channelOptions.length}개)`
                : `(${selectedChannels.length}개 선택)`}
            </span>
          </div>
          {channelOptions.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              등록 채널 없음.{" "}
              <Link href="/admin/settings" className="text-fin-gold hover:underline">
                설정
              </Link>
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {channelOptions.map((c) => {
                const checked = selectedChannels.includes(c.key);
                return (
                  <label
                    key={c.key}
                    className={
                      "inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs " +
                      (checked
                        ? "border-fin-gold/50 bg-fin-gold/10 text-foreground"
                        : "border-border text-muted-foreground hover:bg-muted/40")
                    }
                  >
                    <input
                      type="checkbox"
                      name="channel"
                      value={c.key}
                      defaultChecked={checked}
                      className="size-3.5 accent-[var(--fin-gold)]"
                    />
                    <span className="max-w-[10rem] truncate" title={c.label}>
                      {c.label}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      </form>

      <YoutubeOpsBar
        filterFrom={from}
        filterTo={to}
        channelIds={selectedChannels}
      />

      <div className="flex gap-1 border-b border-border">
        <Link
          href={tabHref("pending", from, to, selectedChannels)}
          className={
            "border-b-2 px-4 py-2 text-sm font-medium -mb-px " +
            (tab === "pending"
              ? "border-fin-gold text-fin-gold"
              : "border-transparent text-muted-foreground hover:text-foreground")
          }
        >
          대기{pending ? ` (${pendingReady})` : ""}
        </Link>
        <Link
          href={tabHref("result", from, to, selectedChannels)}
          className={
            "border-b-2 px-4 py-2 text-sm font-medium -mb-px " +
            (tab === "result"
              ? "border-fin-gold text-fin-gold"
              : "border-transparent text-muted-foreground hover:text-foreground")
          }
        >
          요약완료{summaries ? ` (${summaries.length})` : ""}
        </Link>
      </div>

      {error ? (
        <p className="text-sm text-destructive">
          데이터를 불러오지 못했습니다. api(:8000)를 확인하세요.
        </p>
      ) : tab === "pending" ? (
        <YoutubePendingTable
          items={pending || []}
          filterFrom={from}
          filterTo={to}
          channelIds={selectedChannels}
        />
      ) : summaries && summaries.length === 0 ? (
        <div className="space-y-2 text-sm text-muted-foreground">
          <p>
            조건에 맞는 <b className="text-foreground/80">요약완료</b> 결과가 없습니다.
          </p>
          <p>
            대본을 가져온 뒤 <b className="text-fin-gold">대기</b> 탭에서 요약하세요.
            {pendingReady > 0
              ? ` (지금 미요약 ${pendingReady}건)`
              : " 상단 「최근 수집」 후 대기 탭을 확인하세요."}
          </p>
        </div>
      ) : (
        <YoutubeSummaryTable items={summaries || []} />
      )}

      <p className="text-xs text-muted-foreground">
        종목:{" "}
        <Link
          className="text-fin-gold hover:underline"
          href={stockHubHref("017670", { tab: "youtube", name: "SK텔레콤" })}
        >
          SK텔레콤
        </Link>
        {" · "}
        <Link
          className="text-fin-gold hover:underline"
          href={stockHubHref("000660", { tab: "youtube", name: "SK하이닉스" })}
        >
          SK하이닉스
        </Link>
      </p>
    </div>
  );
}
