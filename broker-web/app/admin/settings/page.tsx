"use client";

import { useState } from "react";
import { TelegramPanel } from "@/components/admin/telegram-panel";
import { CloseBetPanel } from "@/components/admin/close-bet-panel";
import { YoutubePanel } from "@/components/admin/youtube-panel";
import { PullbackPanel } from "@/components/admin/pullback-panel";

type Tab = "telegram" | "closebet" | "pullback" | "youtube";

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
        <TabButton active={tab === "pullback"} onClick={() => setTab("pullback")}>
          눌림목 매매
        </TabButton>
        <TabButton active={tab === "youtube"} onClick={() => setTab("youtube")}>
          유튜브
        </TabButton>
      </div>

      {tab === "telegram" && <TelegramPanel />}
      {tab === "closebet" && <CloseBetPanel />}
      {tab === "pullback" && <PullbackPanel />}
      {tab === "youtube" && <YoutubePanel />}
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
