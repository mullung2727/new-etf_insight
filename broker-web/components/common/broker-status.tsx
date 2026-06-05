"use client"

import { useBrokerEvents } from "@/lib/use-broker-events";
import { useState } from "react"

export function BrokerStatus() {
    const [connected, setConnected] = useState(false);

    useBrokerEvents((e)=> {
        if (e.channel !== "system") return;
        if (e.payload.type == "connected") setConnected(true);
        if (e.payload.type == "disconnected") setConnected(false);
    });

    return (
        <div className="flex items-center gap-1.5" title={connected ? "키움 WS 연결됨" : "키움 WS 끊김"}>
            <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} />
            <span className="text-xs text-muted-foreground">{connected ? "연결됨" : "끊김"}</span>
        </div>
    );
}