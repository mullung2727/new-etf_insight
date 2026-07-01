"use client"

import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { brokerBase } from "./broker-base";

export type BrokerEvent = {
    channel: string;
    payload: Record<string, unknown>;
};

// 체결 통보 판정: 주문체결 채널(00) + 913 체결구분="체결"
export function isFillEvent(e: BrokerEvent): boolean {
    return e.channel === "00" && e.payload["913"] === "체결";
}

type Subscriber = (e: BrokerEvent) => void;

// 구독자 집합. Provider 밖에서 useBrokerEvents 를 써도 no-op 되도록 기본값 null.
const BrokerEventsContext = createContext<Set<Subscriber> | null>(null);

// 단일 EventSource 를 열고 수신 이벤트를 모든 구독자에게 fan-out.
// 소비자들이 각자 EventSource 를 여는 대신 이 Provider 하나만 연결을 유지한다.
export function BrokerEventsProvider({ children }: { children: ReactNode }) {
    const subs = useRef<Set<Subscriber>>(new Set()).current;

    useEffect(() => {
        const es = new EventSource(`${brokerBase()}/events`);
        es.onmessage = (e: MessageEvent) => {
            let parsed: BrokerEvent;
            try {
                parsed = JSON.parse(e.data) as BrokerEvent;
            } catch {
                return; // keepalive ping 등 비JSON 무시
            }
            subs.forEach((cb) => cb(parsed));
        };
        return () => es.close();
    }, [subs]);

    return <BrokerEventsContext.Provider value={subs}>{children}</BrokerEventsContext.Provider>;
}

export function useBrokerEvents(onEvent: Subscriber): void {
    const subs = useContext(BrokerEventsContext);
    // 콜백을 ref 로 감싸 매 렌더 최신 클로저를 쓰되 재구독은 하지 않음 (stale 방지)
    const cbRef = useRef(onEvent);
    cbRef.current = onEvent;

    useEffect(() => {
        if (!subs) return; // Provider 밖: no-op
        const sub: Subscriber = (e) => cbRef.current(e);
        subs.add(sub);
        return () => { subs.delete(sub); };
    }, [subs]);
}
