"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronUp } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatDate, formatThemeStatus, formatThemeBucket } from "@/lib/formatters";
import type { EtfDetail, Holding } from "@/lib/queries";

export function EtfDetailView({ detail, holdings }: { detail: EtfDetail; holdings: Holding[] }) {
  const [descExpanded, setDescExpanded] = useState(false);

  return (
    <div className="space-y-6">
      <Link href="/etfs" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "-ml-2")}>
        ← 목록으로
      </Link>

      <Card>
        <CardHeader>
          <CardTitle data-testid="fund-name" className="text-xl leading-snug">{detail.fund_name ?? "-"}</CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>{detail.asset_manager ?? "-"}</span>
            {detail.primary_country && <Badge variant="outline">{detail.primary_country}</Badge>}
            {detail.theme_status && <Badge>{formatThemeStatus(detail.theme_status)}</Badge>}
          </div>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5">
            <dt className="text-muted-foreground">지수명</dt><dd>{detail.index_name ?? "-"}</dd>
            {detail.index_provider && (<><dt className="text-muted-foreground">지수 제공사</dt><dd>{detail.index_provider}</dd></>)}
            <dt className="text-muted-foreground">최초공시일</dt><dd>{formatDate(detail.first_rcept_dt)}</dd>
            <dt className="text-muted-foreground">수정횟수</dt><dd>{detail.revision_count ?? 0}회</dd>
            <dt className="text-muted-foreground">테마 그룹</dt><dd>{formatThemeBucket(detail.theme_bucket)}</dd>
            {typeof detail.classification_confidence === "number" && (
              <><dt className="text-muted-foreground">분류 신뢰도</dt><dd>{Math.round(detail.classification_confidence * 100)}%</dd></>
            )}
            {detail.structure_tags && detail.structure_tags.length > 0 && (
              <><dt className="text-muted-foreground">구조 태그</dt><dd className="flex flex-wrap gap-1">{detail.structure_tags.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</dd></>
            )}
          </dl>
          {detail.index_description && (
            <div className="pt-1 border-t">
              <button type="button" onClick={() => setDescExpanded((v) => !v)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
                지수 설명 {descExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>
              {descExpanded && <p className="mt-2 text-sm leading-relaxed text-muted-foreground whitespace-pre-wrap">{detail.index_description}</p>}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">구성종목</CardTitle></CardHeader>
        <CardContent>
          {holdings.length > 0 ? (
            <Table data-testid="holdings-table">
              <TableHeader><TableRow><TableHead>종목명</TableHead><TableHead>비중</TableHead><TableHead>티커</TableHead><TableHead>거래소</TableHead></TableRow></TableHeader>
              <TableBody>
                {holdings.map((h, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium">{h.name}</TableCell>
                    <TableCell>{h.weight ?? "-"}</TableCell>
                    <TableCell className="text-muted-foreground">{h.ticker ?? "-"}</TableCell>
                    <TableCell className="text-muted-foreground">{h.exchange ?? "-"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p data-testid="holdings-fallback" className="text-sm text-muted-foreground">{detail.holdings_summary || "구성종목 정보 없음"}</p>
          )}
        </CardContent>
      </Card>

      <Card data-testid="trend-summary">
        <CardHeader><CardTitle className="text-base">AI 분석</CardTitle></CardHeader>
        <CardContent className="space-y-4 text-sm">
          {detail.keywords && detail.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1.5">{detail.keywords.map((kw) => <Badge key={kw} variant="secondary">{kw}</Badge>)}</div>
          )}
          {detail.trend_summary ? (
            <div><p className="text-xs text-muted-foreground mb-1">트렌드 요약</p><p className="leading-relaxed">{detail.trend_summary}</p></div>
          ) : <p className="text-muted-foreground">분석 정보 없음</p>}
          {detail.classification_evidence && (
            <div><p className="text-xs text-muted-foreground mb-1">분류 근거</p><p className="leading-relaxed">{detail.classification_evidence}</p></div>
          )}
          {detail.missing_info && detail.missing_info.length > 0 && (
            <div><p className="text-xs text-muted-foreground mb-1">누락 정보</p><ul className="list-disc list-inside space-y-0.5 text-muted-foreground">{detail.missing_info.map((item, i) => <li key={i}>{item}</li>)}</ul></div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
