"use client";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { formatKrw } from "@/lib/formatters";
import type { Note, NoteStatus } from "@/lib/broker-client";

const STATUS_LABEL: Record<NoteStatus, string> = {
  open: "진행중",
  partial: "분할매도",
  closed: "종료",
};

const STATUS_CLASS: Record<NoteStatus, string> = {
  open: "text-blue-400 border-blue-400/30",
  partial: "text-yellow-400 border-yellow-400/30",
  closed: "text-muted-foreground border-border",
};

interface NoteCardProps {
  note: Note;
  onClick: () => void;
}

export function NoteCard({ note, onClick }: NoteCardProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className="flex flex-col gap-2 rounded-lg border border-border p-4 cursor-pointer hover:bg-muted/30 transition-colors"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono font-semibold text-sm">{note.symbol}</span>
        <Badge variant="outline" className={cn("text-xs", STATUS_CLASS[note.status])}>
          {STATUS_LABEL[note.status]}
        </Badge>
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
