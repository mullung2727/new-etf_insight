"use client";

import { useEffect, useState } from "react";
import { brokerClient, type Note, type NotePnl, type NoteStatus } from "@/lib/broker-client";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { NoteCard } from "./note-card";
import { NoteModal } from "./note-modal";

export interface NotesPanelProps {
  symbol?: string;
}

// 화면 열려있는 동안만 손익 폴링(보유수량 있는 노트만 서버가 배치조회).
const PNL_POLL_MS = 15000;

export function NotesPanel({ symbol }: NotesPanelProps) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [pnlByUid, setPnlByUid] = useState<Record<string, NotePnl>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<NoteStatus | "">("");
  const [modalUid, setModalUid] = useState<string | "new" | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    brokerClient
      .listNotes({ symbol, status: statusFilter || undefined })
      .then(setNotes)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  const loadPnl = () => {
    brokerClient
      .getNotesPnlSummary({ symbol, status: statusFilter || undefined })
      .then((items) => setPnlByUid(Object.fromEntries(items.map((i) => [i.uid, i.pnl]))))
      .catch(() => {}); // 손익은 부가정보 — 실패해도 목록 자체는 그대로 보여준다
  };

  useEffect(() => {
    load();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, statusFilter]);

  useEffect(() => {
    loadPnl();
    const id = setInterval(loadPnl, PNL_POLL_MS);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, statusFilter]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">
          {symbol ? `${symbol} 투자노트` : "투자노트"}
        </h2>
        <Button size="sm" onClick={() => setModalUid("new")}>새 노트</Button>
      </div>

      {!symbol && (
        <Tabs value={statusFilter} onValueChange={(v) => setStatusFilter(v as NoteStatus | "")}>
          <TabsList>
            <TabsTrigger value="">전체</TabsTrigger>
            <TabsTrigger value="idea">관심</TabsTrigger>
            <TabsTrigger value="open">진행중</TabsTrigger>
            <TabsTrigger value="partial">분할매도</TabsTrigger>
            <TabsTrigger value="closed">종료</TabsTrigger>
          </TabsList>
        </Tabs>
      )}

      {error && <p className="text-xs text-status-loss">{error}</p>}

      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : notes.length === 0 ? (
        <p className="text-sm text-muted-foreground">노트가 없습니다.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {notes.map((note) => (
            <NoteCard
              key={note.uid}
              note={note}
              pnl={pnlByUid[note.uid]}
              onClick={() => setModalUid(note.uid)}
            />
          ))}
        </div>
      )}

      <NoteModal
        uid={modalUid}
        symbol={symbol}
        onClose={() => setModalUid(null)}
        onSaved={load}
      />
    </div>
  );
}
