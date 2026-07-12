import Link from "next/link";
import { getTelegramMentions, getThemePeers, type TelegramMention, type ThemePeer } from "@/lib/queries";
import { stockHubHref } from "@/lib/stock-hub";
import TelegramFilter from "@/components/stock/telegram-filter";

const SESSION_LABEL: Record<string, string> = {
  morning: "장전", close: "종가", evening: "장마감후",
};
const CHANGE_LABEL: Record<string, string> = { new: "🆕 신규", continued: "🔁 지속" };

async function load(code: string, session: string): Promise<TelegramMention[] | null> {
  try {
    return await getTelegramMentions(code, { session: session || undefined });
  } catch (err) {
    console.error(`${code} 텔레그램 언급 조회 에러:`, err);
    return null;
  }
}

async function loadPeers(code: string): Promise<ThemePeer[]> {
  try {
    return await getThemePeers(code);
  } catch (err) {
    console.error(`${code} 테마 피어 조회 에러:`, err);
    return [];
  }
}

export default async function TelegramTab({
  code,
  session,
}: {
  code: string;
  session: string;
}) {
  const [mentions, peers] = await Promise.all([load(code, session), loadPeers(code)]);

  return (
    <div>
      <TelegramFilter session={session} />

      {mentions === null ? (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          언급 이력을 불러올 수 없습니다
        </div>
      ) : mentions.length === 0 ? (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          이 종목의 텔레그램 언급이 없습니다
        </div>
      ) : (
        <ul className="flex flex-col divide-y divide-primary/10">
          {mentions.map((m) => (
            <li key={`${m.date_kst}-${m.session}`} className="flex flex-col gap-2 py-3 first:pt-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] text-white/70">{m.date_kst}</span>
                <span className="text-[11px] tracking-[0.1em] text-white/40 border border-white/15 rounded px-1.5 py-0.5">
                  {SESSION_LABEL[m.session] ?? m.session}
                </span>
                {m.change_type && (
                  <span className="text-[12px] text-primary">
                    {CHANGE_LABEL[m.change_type] ?? m.change_type}
                  </span>
                )}
                {m.themes.map((t) => (
                  <span key={t} className="text-[12px] text-primary">#{t}</span>
                ))}
              </div>

              {m.change_summary && (
                <p className="text-[13px] text-white/70 leading-relaxed">{m.change_summary}</p>
              )}

              <div className="flex items-center gap-1.5 flex-wrap">
                {/* 소스채널 뱃지 */}
                {m.channels.map((ch) => (
                  <span
                    key={ch}
                    className="text-[11px] tracking-[0.05em] text-white/50 border border-primary/20 rounded px-1.5 py-0.5"
                  >
                    {ch}
                  </span>
                ))}
                {/* 원문링크 */}
                {m.post_refs.map((ref) => (
                  <a
                    key={ref}
                    href={`https://t.me/${ref}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] tracking-[0.05em] text-fin-gold hover:underline"
                  >
                    원문↗
                  </a>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* 같은 테마 언급 종목 */}
      {peers.length > 0 && (
        <div className="mt-10 pt-6 border-t border-primary/10">
          <div className="text-[11px] tracking-[0.2em] text-white/40 mb-3">같은 테마 언급 종목</div>
          <ul className="flex flex-col gap-2">
            {peers.map((p) => (
              <li key={p.ticker} className="flex items-center gap-2 flex-wrap">
                <Link
                  href={stockHubHref(p.ticker, { tab: "telegram", name: p.name })}
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
