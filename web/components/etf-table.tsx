"use client";

import { useRouter } from "next/navigation";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { EtfListItem } from "@/lib/queries";

interface EtfTableProps {
  etfs: EtfListItem[];
}

function formatDate(s: string | null): string {
  if (!s || s.length !== 8) return s ?? "-";
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}

export function EtfTable({ etfs }: EtfTableProps) {
  const router = useRouter();

  if (etfs.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        조건에 맞는 ETF가 없습니다.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>펀드명</TableHead>
          <TableHead>운용사</TableHead>
          <TableHead>지수명</TableHead>
          <TableHead>국가</TableHead>
          <TableHead>최초 공시일</TableHead>
          <TableHead>수정횟수</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {etfs.map((etf) => (
          <TableRow
            key={etf.etf_key}
            className="cursor-pointer"
            onClick={() => router.push(`/etfs/${etf.etf_key}`)}
          >
            <TableCell className="max-w-64 truncate font-medium">
              {etf.fund_name ?? "-"}
            </TableCell>
            <TableCell>{etf.asset_manager ?? "-"}</TableCell>
            <TableCell className="max-w-48 truncate">
              {etf.index_name ?? "-"}
            </TableCell>
            <TableCell>
              {etf.primary_country ? (
                <Badge variant="outline">{etf.primary_country}</Badge>
              ) : (
                <span className="text-muted-foreground">-</span>
              )}
            </TableCell>
            <TableCell>{formatDate(etf.first_rcept_dt)}</TableCell>
            <TableCell>{etf.revision_count ?? 0}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
