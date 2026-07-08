import type { Holding } from "@/lib/broker-client";
import { NotesPanel } from "@/components/notes/notes-panel";

function num(v: string | undefined): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

// 보유손익 요약(잔고 기준). notes 손익은 NotesPanel이 자체 조회.
function HoldingPnl({ holding }: { holding: Holding }) {
  const prft = num(holding.evltv_prft);
  const rate = num(holding.prft_rt);
  const tone = prft > 0 ? "text-up" : prft < 0 ? "text-down" : "text-white/60";
  const sign = prft > 0 ? "+" : "";
  return (
    <div className="mb-6 border border-primary/20 rounded p-4 flex items-center gap-4 flex-wrap">
      <span className="text-[11px] tracking-[0.2em] text-white/40">보유손익</span>
      <span className={`text-lg font-bold ${tone}`}>
        {sign}{prft.toLocaleString()}
      </span>
      <span className={`text-sm ${tone}`}>
        {sign}{rate.toFixed(2)}%
      </span>
      <span className="text-[12px] text-white/40">
        {num(holding.rmnd_qty).toLocaleString()}주
      </span>
    </div>
  );
}

export default function NotesTab({ code, holding }: { code: string; holding: Holding | null }) {
  return (
    <div>
      {holding && <HoldingPnl holding={holding} />}
      <NotesPanel symbol={code} />
    </div>
  );
}
