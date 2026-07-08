"use client";

import { useState } from "react";
import { TelegramPanel } from "@/components/admin/telegram-panel";
import { CloseBetPanel } from "@/components/admin/close-bet-panel";

type Tab = "telegram" | "closebet";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("telegram");

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <h1 className="text-lg font-semibold">설정</h1>

      <div className="flex gap-1 border-b border-border">
        <TabButton active={tab === "telegram"} onClick={() => setTab("telegram")}>
          텔레그램
        </TabButton>
        <TabButton active={tab === "closebet"} onClick={() => setTab("closebet")}>
          종가베팅
        </TabButton>
      </div>

      {tab === "telegram" && <TelegramPanel />}
      {tab === "closebet" && <CloseBetPanel />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "border-b-2 px-4 py-2 text-sm font-medium -mb-px " +
        (active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  );
}
