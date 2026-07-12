import { getYoutubeMentions, type YoutubeMention } from "@/lib/queries";

async function load(code: string): Promise<YoutubeMention[] | null> {
  try {
    return await getYoutubeMentions(code);
  } catch (err) {
    console.error(`${code} 유튜브 언급 조회 에러:`, err);
    return null;
  }
}

export default async function YoutubeTab({ code }: { code: string }) {
  const mentions = await load(code);

  return (
    <div>
      <p className="mb-4 text-[12px] tracking-[0.05em] text-white/40">
        유튜브 영상 요약 기반 종목 시그널 (discovery 채널). 텔레그램 DB와 분리 저장, 같은 종목 코드로 조회.
      </p>

      {mentions === null ? (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          언급 이력을 불러올 수 없습니다
        </div>
      ) : mentions.length === 0 ? (
        <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
          이 종목의 유튜브 언급이 없습니다
        </div>
      ) : (
        <ul className="flex flex-col divide-y divide-primary/10">
          {mentions.map((m) => (
            <li key={m.date_kst} className="flex flex-col gap-2 py-3 first:pt-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] text-white/70">{m.date_kst}</span>
                <span className="text-[11px] tracking-[0.1em] text-fin-gold border border-fin-gold/40 rounded px-1.5 py-0.5">
                  유튜브
                </span>
                <span className="text-[12px] text-white/50">{m.name}</span>
              </div>

              {(m.analysis || m.discovery_reason) && (
                <p className="text-[13px] text-white/70 leading-relaxed">
                  {m.analysis || m.discovery_reason}
                </p>
              )}

              <div className="flex items-center gap-1.5 flex-wrap">
                {m.channels.map((ch) => (
                  <span
                    key={ch}
                    className="text-[11px] tracking-[0.05em] text-white/50 border border-primary/20 rounded px-1.5 py-0.5 font-mono"
                  >
                    {ch.length > 12 ? `${ch.slice(0, 10)}…` : ch}
                  </span>
                ))}
                {m.video_ids.map((vid) => (
                  <a
                    key={vid}
                    href={`https://www.youtube.com/watch?v=${vid}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] tracking-[0.05em] text-fin-gold hover:underline"
                  >
                    영상↗
                  </a>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
