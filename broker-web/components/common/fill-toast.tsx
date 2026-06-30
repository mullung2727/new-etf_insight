"use client"

import { useBrokerEvents } from "@/lib/use-broker-events";
import { useState } from "react";
import { NoteModal } from "../notes/note-modal";
import { Button } from "../ui/button";

interface Fill {
    id: number;
    symbol: string;
    side:string;
    price: string;
    qty: string;
}

export function FillToast() {
    const [fills, setFills] = useState<Fill[]>([]);
    const [noteSymbol, setNoteSymbol] = useState<string | null>(null);

    useBrokerEvents((e)=> {
        if(e.channel !== "00") return;
        if (e.payload["913"] !== "체결") return;
        setFills((prev)=> [
            ...prev,
            {
                id: Date.now(),
                symbol: String(e.payload["9001"] ?? ""),
                side:   String(e.payload["905"]  ?? ""),
                price:  String(e.payload["910"]  ?? ""),
                qty:    String(e.payload["911"]  ?? ""),
            },
        ])
    })

    const dismiss = (id:number) => setFills((prev)=>prev.filter((f)=>f.id!==id));

    return (
    <>
      <div className="fixed bottom-4 right-4 flex flex-col gap-2 z-50">
        {fills.map((fill) => (
          <div
            key={fill.id}
            className="bg-card border border-border rounded-lg shadow-lg p-4 w-72 flex flex-col gap-2"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono font-semibold">{fill.symbol}</span>
              <span className="text-xs text-muted-foreground">
                {/* 키움 FID 905 매도수구분: 1=매도, 2=매수 (kt00007 sell_tp 동일). "+매수" 변형도 방어 */}
                {fill.side === "2" || fill.side.includes("매수") ? "매수" : "매도"} 체결
              </span>
            </div>
            <div className="text-sm tabular-nums">
              <span className="text-muted-foreground">체결가 </span>
              {Number(fill.price).toLocaleString()}원
              <span className="text-muted-foreground ml-3">수량 </span>
              {fill.qty}주
            </div>
            <div className="flex gap-2 justify-end">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setNoteSymbol(fill.symbol);
                  dismiss(fill.id);
                }}
              >
                노트 작성
              </Button>
              <Button size="sm" variant="ghost" onClick={() => dismiss(fill.id)}>
                닫기
              </Button>
            </div>
          </div>
        ))}
      </div>

      <NoteModal
        uid={noteSymbol !== null ? "new" : null}
        symbol={noteSymbol ?? undefined}
        onClose={() => setNoteSymbol(null)}
        onSaved={() => setNoteSymbol(null)}
      />
    </> 
    )
}