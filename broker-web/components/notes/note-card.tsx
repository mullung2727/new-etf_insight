"use client";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { formatKrw, formatPercent } from "@/lib/formatters";
import type { Note, NotePnl, NoteStatus } from "@/lib/broker-client";

const STATUS_LABEL: Record<NoteStatus, string> = {
  open: "진행중",
  partial: "분할매도",
  closed: "종료",
};

const STATUS_CLASS: Record<NoteStatus, string> = {
  open: "text-status-open border-status-open/30",
  partial: "text-status-partial border-status-partial/30",
  closed: "text-muted-foreground border-border",
};

interface NoteCardProps {
  note: Note;
  pnl?: NotePnl;
  onClick: () => void;
}

export function NoteCard({ note, pnl, onClick }: NoteCardProps) {
  const pnlColor =
    pnl == null ? "text-muted-foreground" : pnl.net_pnl > 0 ? "text-status-profit" : pnl.net_pnl < 0 ? "text-status-loss" : "text-muted-foreground";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className="flex flex-col gap-2 rounded-lg border border-border p-4 cursor-pointer hover:bg-muted/30 transition-colors"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-baseline gap-1.5">
          {note.name && <span className="font-semibold text-sm">{note.name}</span>}
          <span className="font-mono text-xs text-muted-foreground">{note.symbol}</span>
        </span>
        <div className="flex items-center gap-2">
          {pnl && (
            <span className={cn("text-xs font-medium tabular-nums", pnlColor)}>
              {formatPercent(pnl.net_pnl_pct)} {pnl.net_pnl >= 0 ? "▲" : "▼"}
              {formatKrw(Math.abs(pnl.net_pnl))}
            </span>
          )}
          <Badge variant="outline" className={cn("text-xs", STATUS_CLASS[note.status])}>
            {STATUS_LABEL[note.status]}
          </Badge>
        </div>
      </div>
      {note.buy_reason && (
        <p className="text-sm text-foreground/80 line-clamp-1">{note.buy_reason}</p>
      )}
      <div className="flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
        {note.target_price != null && <span>목표가 {formatKrw(note.target_price)}</span>}
        {note.holding_period && <span>{note.holding_period}</span>}
        <span className="ml-auto">{note.created_at.slice(0, 10)}</span>
      </div>
    </div>
  );
}
