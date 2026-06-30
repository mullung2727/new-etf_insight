"use client";

import { useEffect, useState } from "react";
import { brokerClient } from "@/lib/broker-client";
import { useBrokerEvents } from "@/lib/use-broker-events";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { AccountPanel } from "./account-panel";
import { PendingOrders } from "./pending-orders";

export function AccountTabs() {
  // 미체결 건수는 탭 닫혀도 보여야 하므로 헤더에서 상시 집계 (목록 본체는 탭 열 때만 로드)
  const [count, setCount] = useState(0);

  const refreshCount = async () => {
    try {
      setCount((await brokerClient.listUnfilled("all")).length);
    } catch {
      // 카운트 실패는 무시 (배지만 못 갱신)
    }
  };

  useEffect(() => { refreshCount(); }, []);
  useBrokerEvents((e) => { if (e.channel === "00") refreshCount(); });

  return (
    <Tabs defaultValue="balance" className="px-8 py-5 text-[15px]">
      <TabsList className="w-full">
        <TabsTrigger value="balance" className="text-base">잔고</TabsTrigger>
        <TabsTrigger value="pending" className="text-base">
          미체결
          {count > 0 && <Badge variant="secondary" className="ml-1">{count}</Badge>}
        </TabsTrigger>
        {/* TODO: 거래내역 탭 (kt00007/kt00009) — 범위 밖 */}
      </TabsList>

      <TabsContent value="balance">
        <AccountPanel />
      </TabsContent>
      <TabsContent value="pending">
        <PendingOrders />
      </TabsContent>
    </Tabs>
  );
}
