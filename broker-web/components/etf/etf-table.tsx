"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatDate, formatThemeStatus } from "@/lib/formatters";
import type { EtfListItem } from "@/lib/queries";

export function EtfTable({ etfs }: { etfs: EtfListItem[] }) {
  const router = useRouter();
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const selectableKeys = etfs.filter((e) => e.has_holdings).map((e) => e.etf_key);

  useEffect(() => {
    const current = new Set(selectableKeys);
    setSelectedKeys((prev) => {
      const pruned = new Set([...prev].filter((k) => current.has(k)));
      return pruned.size === prev.size ? prev : pruned;
    });
  }, [etfs]); // eslint-disable-line react-hooks/exhaustive-deps

  const allSelected = selectableKeys.length > 0 && selectableKeys.every((k) => selectedKeys.has(k));
  const someSelected = !allSelected && selectableKeys.some((k) => selectedKeys.has(k));

  const toggleAll = () => setSelectedKeys(allSelected ? new Set() : new Set(selectableKeys));
  const toggleKey = (key: string) => setSelectedKeys((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });

  if (etfs.length === 0) return <p className="py-12 text-center text-sm text-muted-foreground">조건에 맞는 ETF가 없습니다.</p>;

  return (
    <div className="space-y-2">
      {selectedKeys.size > 0 && (
        <div className="flex items-center justify-between rounded-md bg-muted px-3 py-2 text-sm">
          <span className="text-muted-foreground">{selectedKeys.size}개 선택됨</span>
          <button className={cn(buttonVariants({ variant: "default", size: "sm" }))} onClick={() => router.push(`/stats?keys=${Array.from(selectedKeys).join(",")}`)}>
            통계 보기
          </button>
        </div>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">
              <input type="checkbox" checked={allSelected} ref={(el) => { if (el) el.indeterminate = someSelected; }} onChange={toggleAll} className="cursor-pointer" aria-label="전체 선택" />
            </TableHead>
            <TableHead>펀드명</TableHead><TableHead>운용사</TableHead><TableHead>지수명</TableHead>
            <TableHead>국가</TableHead><TableHead>테마</TableHead><TableHead>최초 공시일</TableHead>
            <TableHead>수정횟수</TableHead><TableHead>구성종목</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {etfs.map((etf) => (
            <TableRow key={etf.etf_key} className="cursor-pointer" onClick={() => router.push(`/etfs/${etf.etf_key}`)}>
              <TableCell onClick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={selectedKeys.has(etf.etf_key)} disabled={!etf.has_holdings} onChange={() => toggleKey(etf.etf_key)} className={cn("cursor-pointer", !etf.has_holdings && "cursor-not-allowed opacity-30")} aria-label={etf.fund_name ?? etf.etf_key} />
              </TableCell>
              <TableCell className="max-w-64 truncate font-medium">{etf.fund_name ?? "-"}</TableCell>
              <TableCell>{etf.asset_manager ?? "-"}</TableCell>
              <TableCell className="max-w-48 truncate">{etf.index_name ?? "-"}</TableCell>
              <TableCell>{etf.primary_country ? <Badge variant="outline">{etf.primary_country}</Badge> : <span className="text-muted-foreground">-</span>}</TableCell>
              <TableCell>{etf.theme_status ? <Badge variant={etf.theme_status === "theme" ? "default" : "outline"}>{formatThemeStatus(etf.theme_status)}</Badge> : <span className="text-muted-foreground">-</span>}</TableCell>
              <TableCell>{formatDate(etf.first_rcept_dt)}</TableCell>
              <TableCell>{etf.revision_count ?? 0}</TableCell>
              <TableCell>{etf.has_holdings ? <Badge variant="secondary">있음</Badge> : <span className="text-muted-foreground">-</span>}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
