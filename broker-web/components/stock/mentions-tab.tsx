"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  getTelegramMentions,
  getYoutubeMentions,
  getThemePeers,
  type TelegramMention,
  type YoutubeMention,
  type ThemePeer,
} from "@/lib/queries";
import { stockHubHref } from "@/lib/stock-hub";

type Source = "tg" | "yt";
type Row = {
  key: string;
  date_kst: string;
  source: Source;
  summary: string | null;
  session?: string;
  changeType?: string | null;
  themes?: string[];
  badges: string[];
  links: { href: string; label: string }[];
};

const SESSION_LABEL: Record<string, string> = { morning: "장전", close: "종가", evening: "장마감후" };
const CHANGE_LABEL: Record<string, string> = { new: "🆕 신규", continued: "🔁 지속" };
const SOURCE_ORDER: Record<Source, number> = { tg: 0, yt: 1 };

function tgRow(m: TelegramMention, i: number): Row {
  return {
    key: `tg-${m.date_kst}-${m.session}-${i}`,
    date_kst: m.date_kst,
    source: "tg",
    summary: m.change_summary,
    session: m.session,
    changeType: m.change_type,
    themes: m.themes,
    badges: m.channels,
    links: m.post_refs.map((ref) => ({ href: `https://t.me/${ref}`, label: "원문↗" })),
  };
}

function ytRow(m: YoutubeMention, i: number): Row {
  return {
    key: `yt-${m.date_kst}-${i}`,
    date_kst: m.date_kst,
    source: "yt",
    summary: m.analysis || m.discovery_reason,
    badges: m.channels,
    links: m.video_ids.map((v) => ({ href: `https://www.youtube.com/watch?v=${v}`, label: "영상↗" })),
  };
}

// 날짜 desc, 동일 날짜는 소스 우선순위(tg→yt) 고정.
function sortRows(rows: Row[]): Row[] {
  return rows.sort((a, b) =>
    a.date_kst < b.date_kst ? 1 : a.date_kst > b.date_kst ? -1 : SOURCE_ORDER[a.source] - SOURCE_ORDER[b.source]
  );
}

const FILTERS = [
  { id: "all", label: "전체" },
  { id: "tg", label: "텔레그램" },
  { id: "yt", label: "유튜브" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

// 종목 언급 통합 탭. 텔레그램+유튜브 언급을 한 타임라인으로. 한쪽 장애는 나머지만 표시.
export default function MentionsTab({ code, session }: { code: string; session?: string }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [peers, setPeers] = useState<ThemePeer[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [tg, yt, pr] = await Promise.allSettled([
        getTelegramMentions(code, { session: session || undefined }),
        getYoutubeMentions(code),
        getThemePeers(code),
      ]);
      if (!alive) return;
      const next: Row[] = [];
      if (tg.status === "fulfilled") next.push(...tg.value.map(tgRow));
      if (yt.status === "fulfilled") next.push(...yt.value.map(ytRow));
      setRows(sortRows(next));
      setPeers(pr.status === "fulfilled" ? pr.value : []);
      // 두 소스 모두 실패해야 에러(한쪽만 실패는 나머지 표시)
      setError(tg.status === "rejected" && yt.status === "rejected");
      setLoaded(true);
    })();
    return () => {
      alive = false;
    };
    // code별 remount(부모 key)로 재조회. deps 비움.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const shown = filter === "all" ? rows : rows.filter((r) => r.source === filter);

  return (
    <div>
      {/* 소스 필터 */}
      <div className="mb-4 flex items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            data-testid={`mention-filter-${f.id}`}
            onClick={() => setFilter(f.id)}
            aria-pressed={filter === f.id}
            className={
              "px-3 py-1 text-[12px] tracking-[0.1em] rounded border transition-colors " +
              (filter === f.id
                ? "border-primary/60 text-primary bg-primary/[0.08]"
                : "border-white/15 text-white/40 hover:text-white/70")
            }
          >
            {f.label}
          </button>
        ))}
      </div>

      {error ? (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          언급 이력을 불러올 수 없습니다
        </div>
      ) : loaded && shown.length === 0 ? (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          이 종목의 언급이 없습니다
        </div>
      ) : (
        <ul className="flex flex-col divide-y divide-primary/10">
          {shown.map((r) => (
            <li key={r.key} data-testid="mention-row" data-source={r.source} className="flex flex-col gap-2 py-3 first:pt-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] text-white/70">{r.date_kst}</span>
                {r.source === "yt" ? (
                  <span className="text-[11px] tracking-[0.1em] text-fin-gold border border-fin-gold/40 rounded px-1.5 py-0.5">
                    유튜브
                  </span>
                ) : (
                  <span className="text-[11px] tracking-[0.1em] text-primary border border-primary/30 rounded px-1.5 py-0.5">
                    텔레그램
                  </span>
                )}
                {r.session && (
                  <span className="text-[11px] tracking-[0.1em] text-white/40 border border-white/15 rounded px-1.5 py-0.5">
                    {SESSION_LABEL[r.session] ?? r.session}
                  </span>
                )}
                {r.changeType && (
                  <span className="text-[12px] text-primary">{CHANGE_LABEL[r.changeType] ?? r.changeType}</span>
                )}
                {r.themes?.map((t) => (
                  <span key={t} className="text-[12px] text-primary">#{t}</span>
                ))}
              </div>

              {r.summary && <p className="text-[13px] text-white/70 leading-relaxed">{r.summary}</p>}

              <div className="flex items-center gap-1.5 flex-wrap">
                {r.badges.map((ch) => (
                  <span
                    key={ch}
                    className="text-[11px] tracking-[0.05em] text-white/50 border border-primary/20 rounded px-1.5 py-0.5"
                  >
                    {ch.length > 12 ? `${ch.slice(0, 10)}…` : ch}
                  </span>
                ))}
                {r.links.map((l) => (
                  <a
                    key={l.href}
                    href={l.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] tracking-[0.05em] text-fin-gold hover:underline"
                  >
                    {l.label}
                  </a>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* 같은 테마 언급 종목 (텔레그램) */}
      {peers.length > 0 && (
        <div className="mt-10 pt-6 border-t border-primary/10">
          <div className="text-[11px] tracking-[0.2em] text-white/40 mb-3">같은 테마 언급 종목</div>
          <ul className="flex flex-col gap-2">
            {peers.map((p) => (
              <li key={p.ticker} className="flex items-center gap-2 flex-wrap">
                <Link
                  href={stockHubHref(p.ticker, { tab: "mentions", name: p.name })}
                  className="text-[13px] text-white/80 hover:text-primary transition-colors"
                >
                  {p.name || p.ticker}
                  <span className="text-white/40 font-normal"> ({p.ticker})</span>
                </Link>
                {p.themes.map((t) => (
                  <span key={t} className="text-[12px] text-primary">#{t}</span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
