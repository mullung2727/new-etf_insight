import Link from "next/link";
import { brokerClient, type Holding } from "@/lib/broker-client";
import { apiGet } from "@/lib/api-client";
import { getTelegramMentions } from "@/lib/queries";
import { sortTimeline, toDate, type TimelineEvent } from "@/lib/timeline";
import { stockHubHref } from "@/lib/stock-hub";

type LlmScore = {
  ticker: string;
  name: string | null;
  score: number | null;
  category: string | null;
  reason_summary: string | null;
  final_opinion: string | null;
};

const EVENT_LABEL: Record<string, string> = {
  buy: "매수", add_buy: "추가매수", partial_sell: "일부매도", sell: "매도",
};
const SESSION_LABEL: Record<string, string> = {
  morning: "장전", close: "종가", evening: "장마감후",
};
const KIND_MARK: Record<string, string> = { mention: "💬", trade: "💵", score: "📊" };

async function safe<T>(p: Promise<T>, fb: T): Promise<T> {
  try { return await p; } catch { return fb; }
}

function num(v: string | undefined): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-primary/20 rounded p-4">
      <div className="text-[11px] tracking-[0.2em] text-white/40 mb-2">{title}</div>
      {children}
    </div>
  );
}

export default async function OverviewTab({ code, holding }: { code: string; holding: Holding | null }) {
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const [scores, mentions, notes] = await Promise.all([
    safe(apiGet<LlmScore[]>(`/watchlist/scores/${today}`), [] as LlmScore[]),
    safe(getTelegramMentions(code), [] as Awaited<ReturnType<typeof getTelegramMentions>>),
    safe(brokerClient.listNotes({ symbol: code }), []),
  ]);
  const score = scores.find((s) => s.ticker === code) ?? null;
  const latestNote = notes[0] ?? null;

  // 노트 매매 이벤트(타임라인용). 종목당 노트 수는 보통 소수라 상세 병렬조회.
  const details = await Promise.all(notes.map((n) => safe(brokerClient.getNote(n.uid), null)));
  const tradeEvents: TimelineEvent[] = details
    .flatMap((d) => d?.events ?? [])
    .map((ev) => ({
      date: toDate(ev.executed_at),
      kind: "trade" as const,
      title: `${EVENT_LABEL[ev.event_type] ?? ev.event_type} ${ev.qty}주 @ ${ev.price.toLocaleString()}`,
      detail: ev.memo ?? undefined,
    }));
  const mentionEvents: TimelineEvent[] = mentions.map((m) => ({
    date: m.date_kst,
    kind: "mention" as const,
    title: `텔레그램 언급 · ${m.channels.length}채널${m.change_type ? ` (${m.change_type})` : ""}`,
    detail: m.change_summary ?? undefined,
  }));
  const timeline = sortTimeline([...mentionEvents, ...tradeEvents]).slice(0, 20);

  const prft = holding ? num(holding.evltv_prft) : 0;
  const rate = holding ? num(holding.prft_rt) : 0;
  const tone = prft > 0 ? "text-up" : prft < 0 ? "text-down" : "text-white/60";

  return (
    <div className="flex flex-col gap-8">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* LLM 스코어 */}
        <Card title="LLM 스코어">
          {score ? (
            <div>
              <div className="flex items-center gap-3">
                {score.score != null && <span className="text-2xl font-bold text-primary">{score.score}</span>}
                {score.category && (
                  <span className="text-[11px] tracking-[0.15em] text-white/50 border border-white/15 rounded px-2 py-0.5">
                    {score.category}
                  </span>
                )}
              </div>
              {score.reason_summary && (
                <p className="text-[13px] text-white/70 leading-relaxed mt-2">{score.reason_summary}</p>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-white/30">오늘자 스코어 없음</p>
          )}
        </Card>

        {/* 보유손익 미니 */}
        <Card title="보유손익">
          {holding ? (
            <div className="flex items-baseline gap-3">
              <span className={`text-xl font-bold ${tone}`}>
                {prft > 0 ? "+" : ""}{prft.toLocaleString()}
              </span>
              <span className={`text-sm ${tone}`}>
                {rate > 0 ? "+" : ""}{rate.toFixed(2)}%
              </span>
              <span className="text-[12px] text-white/40">{num(holding.rmnd_qty).toLocaleString()}주</span>
            </div>
          ) : (
            <p className="text-[13px] text-white/30">미보유</p>
          )}
        </Card>

        {/* 최근 텔레그램 언급 3 */}
        <Card title="최근 텔레그램 언급">
          {mentions.length > 0 ? (
            <ul className="flex flex-col gap-1.5">
              {mentions.slice(0, 3).map((m) => (
                <li key={`${m.date_kst}-${m.session}`} className="text-[13px] text-white/70 flex items-center gap-2">
                  <span className="text-white/40">{m.date_kst}</span>
                  <span className="text-[11px] text-white/40">{SESSION_LABEL[m.session] ?? m.session}</span>
                  {m.themes.slice(0, 2).map((t) => (
                    <span key={t} className="text-[12px] text-primary">#{t}</span>
                  ))}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-white/30">언급 없음</p>
          )}
        </Card>

        {/* 최근 노트 1 */}
        <Card title="최근 투자노트">
          {latestNote ? (
            <div>
              <div className="text-[13px] text-white/80">{latestNote.buy_reason ?? latestNote.memo ?? "(내용 없음)"}</div>
              <div className="text-[11px] text-white/40 mt-1">{latestNote.status} · {latestNote.updated_at.slice(0, 10)}</div>
            </div>
          ) : (
            <p className="text-[13px] text-white/30">노트 없음</p>
          )}
        </Card>
      </div>

      {/* 핵심 재무지표: 재무탭 위임(heavy DART 호출 회피) */}
      <Link href={stockHubHref(code, { tab: "financial" })} className="text-[13px] text-primary/70 hover:text-primary underline">
        핵심 재무지표 보기 → 재무 탭
      </Link>

      {/* 이벤트 타임라인 */}
      <div>
        <div className="text-[11px] tracking-[0.2em] text-white/40 mb-3">이벤트 타임라인</div>
        {timeline.length > 0 ? (
          <ul className="flex flex-col divide-y divide-primary/10">
            {timeline.map((e, i) => (
              <li key={i} className="flex items-start gap-3 py-2.5">
                <span className="text-white/30 text-[12px] w-24 shrink-0">{e.date}</span>
                <span className="shrink-0">{KIND_MARK[e.kind]}</span>
                <div className="min-w-0">
                  <div className="text-[13px] text-white/80">{e.title}</div>
                  {e.detail && <div className="text-[12px] text-white/40">{e.detail}</div>}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-white/30">기록된 이벤트 없음</p>
        )}
      </div>
    </div>
  );
}
