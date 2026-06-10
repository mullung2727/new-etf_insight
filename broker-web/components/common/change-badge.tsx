"use client";

import { cn } from "@/lib/utils";

interface ChangeBadgeProps {
  value: number | null | undefined;
  format?: "krw" | "percent";
  className?: string;
}

export function ChangeBadge({ value, format = "percent", className }: ChangeBadgeProps) {
  if (value == null) return <span className={cn("text-muted-foreground", className)}>-</span>;

  const positive = value > 0;
  const color = positive ? "text-status-profit" : value < 0 ? "text-status-loss" : "text-muted-foreground";
  const sign = positive ? "+" : "";

  const display =
    format === "krw"
      ? `${sign}${new Intl.NumberFormat("ko-KR").format(value)}원`
      : `${sign}${value.toFixed(2)}%`;

  return <span className={cn("font-medium tabular-nums", color, className)}>{display}</span>;
}
