"use client";

import { useEffect, useState } from "react";
import { brokerClient, type Settings } from "@/lib/broker-client";

/**
 * 주문 금액 상한(.env MAX_ORDER_AMOUNT) 읽기 전용 표시.
 *
 * 아래 예산 입력값이 이 상한을 넘으면 주문이 broker 가드에서 거부되므로, 예산을
 * 고치는 화면에서 상한이 보여야 한다. 편집은 일부러 막았다 — 상한은 예산이 틀렸을 때
 * 막는 최후 방어선이라, 예산과 같은 화면에서 고칠 수 있으면 오타 한 번에 둘 다 뚫린다.
 */
export function OrderCapNotice() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    brokerClient.getSettings().then(setSettings).catch(() => setFailed(true));
  }, []);

  if (failed) return null; // broker 미기동 — 설정 편집 자체는 막지 않는다
  if (!settings) return null;

  const cap = settings.max_order_amount;
  return (
    <div className="space-y-1 rounded border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
      <div>
        <span className="font-medium text-foreground">
          1회 주문 상한 {cap.toLocaleString("en-US")}원
        </span>
        {" — 아래 종목당 예산은 이 값보다 작아야 한다(시세 변동 여유 포함). 넘으면 주문이 거부된다."}
      </div>
      <div>
        <span className="font-medium text-foreground">이 화면에서는 바꿀 수 없다.</span>
        {" 루트 "}
        <code className="font-mono">.env</code>
        {" 의 "}
        <code className="font-mono">MAX_ORDER_AMOUNT</code>
        {" 를 수정한 뒤 "}
        <span className="font-medium text-foreground">broker 서버를 재시작</span>
        해야 반영된다. (전략 예산이 틀렸을 때 막는 최후 방어선이라 예산과 같은 화면에 두지 않았다.)
      </div>
    </div>
  );
}
