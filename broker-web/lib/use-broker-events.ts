"use client"

import { useEffect } from "react";
import { brokerBase } from "./broker-base";

export type BrokerEvent = {
    channel: string;
    payload: Record<string, unknown>;
};

export function useBrokerEvents(onEvent: (e:BrokerEvent) => void): void {
    useEffect(()=>{
        const es = new EventSource(`${brokerBase()}/events`);

        es.onmessage = (e: MessageEvent) => {
            try {
                onEvent(JSON.parse(e.data) as BrokerEvent);
            } catch {
                // 파싱 실패 무시 (keepalive ping 등 비JSON 수신 시)
            }
        };

        return () => es.close();
    }, [])
}