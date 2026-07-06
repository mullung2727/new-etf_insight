"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { BrokerStatus } from "./broker-status";

const links = [
  { href: "/etfs", label: "ETF 분석" },
  { href: "/trading", label: "트레이딩" },
  { href: "/notes", label: "투자노트" },
  { href: "/financial", label: "재무제표" },
  { href: "/watchlist", label: "watchlist" },
  { href: "/close-bet", label: "종가배팅" },
  { href: "/research", label: "리포트" },
  { href: "/admin/telegram", label: "채널" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="border-b border-border px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-status-profit text-lg">↗</span>
          <span className="font-semibold text-sm">ETF Insight</span>
        </Link>
        <nav className="flex items-center gap-1">
          {links.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "px-3 py-1.5 rounded-md text-sm transition-colors",
                pathname.startsWith(href)
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              )}
            >
              {label}
            </Link>
          ))}
        </nav>
      </div>
      <BrokerStatus />
    </header>
  );
}
