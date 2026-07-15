"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase } from "@/lib/api-base";
import { initialRange } from "@/lib/research-prefs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Report = {
  researchId: string;
  brokerName: string;
  title: string;
  writeDate: string;
  downloaded: boolean;
  pdfKey: string;
};
type ReportsResp = {
  code: string;
  name: string;
  total: number;
  already: number;
  reports: Report[];
};
type Job = {
  job_id: string;
  status: string;
  total: number;
  downloaded: number;
  skipped: number;
  failed: number;
  error?: string | null;
};

function pdfHref(code: string, name: string, r: Report): string {
  const params = new URLSearchParams({
    name, writeDate: r.writeDate, brokerName: r.brokerName, pdfKey: r.pdfKey,
  });
  return `${apiBase()}/research/stock/${code}/reports/${r.researchId}/pdf?${params}`;
}

// 종목 허브 리포트 탭. 검색은 허브 진입 자체가 대신하므로 code/name은 고정 prop.
// 탭 진입 즉시 자동 조회. code 변경 시 부모가 key={code}로 remount → 상태 리셋.
export default function ResearchTab({ code, name }: { code: string; name: string }) {
  const [dateRange, setDateRange] = useState(() => initialRange(new Date(), null));
  const [data, setData] = useState<ReportsResp | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const since = dateRange.since;
  const until = dateRange.until;

  function updateRange(next: { since: string; until: string }) {
    setDateRange(next);
  }

  const loadReports = useCallback(async (opts?: { resetSelection?: boolean }) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ name });
      if (since) params.set("since", since);
      if (until) params.set("until", until);
      const r = await fetch(`${apiBase()}/research/stock/${code}/reports?${params}`);
      if (!r.ok) throw new Error(`조회 실패 (${r.status})`);
      const d: ReportsResp = await r.json();
      setData(d);
      if (opts?.resetSelection ?? true) {
        setSelectedIds(
          new Set(d.reports.filter((x) => !x.downloaded).map((x) => x.researchId))
        );
      } else {
        setSelectedIds((prev) => {
          const stillPending = new Set(
            d.reports.filter((x) => !x.downloaded).map((x) => x.researchId)
          );
          return new Set(Array.from(prev).filter((id) => stillPending.has(id)));
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "오류");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [code, name, since, until]);

  function toggleOne(id: string, on: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAll(on: boolean) {
    setSelectedIds(
      on && data ? new Set(data.reports.map((r) => r.researchId)) : new Set()
    );
  }

  function poll(id: string) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${apiBase()}/research/jobs/${id}`);
        const j: Job = await r.json();
        setJob(j);
        if (j.status !== "running") {
          clearInterval(pollRef.current);
          loadReports({ resetSelection: false });
        }
      } catch {
        clearInterval(pollRef.current);
      }
    }, 1500);
  }

  async function startDownload() {
    if (selectedIds.size === 0) {
      setError("다운로드할 리포트를 선택해줘");
      return;
    }
    setError("");
    try {
      const r = await fetch(`${apiBase()}/research/stock/${code}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          since: since || null,
          until: until || null,
          researchIds: Array.from(selectedIds),
        }),
      });
      if (r.status === 429) {
        setError("동시 다운로드 최대 3개 — 잠시 후 다시 시도");
        return;
      }
      if (!r.ok) throw new Error(`다운로드 시작 실패 (${r.status})`);
      const j: Job = await r.json();
      setJob(j);
      poll(j.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "오류");
    }
  }

  // 진입 시 1회 자동 조회(기본 기간). code 변경은 key remount로 처리하므로 deps 비움.
  // setTimeout(0): 커밋 이후로 미뤄 set-state-in-effect 회피(원본 /research 패턴).
  useEffect(() => {
    const t = window.setTimeout(() => void loadReports(), 0);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const done = job ? job.downloaded + job.skipped + job.failed : 0;
  const pct = job && job.total ? Math.round((done / job.total) * 100) : 0;
  const running = job?.status === "running";
  const rowCount = data?.reports.length ?? 0;
  const allChecked = rowCount > 0 && selectedIds.size === rowCount;
  const someChecked = selectedIds.size > 0 && !allChecked;

  return (
    <div className="space-y-6">
      {/* 기간 + 조회/다운로드 */}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-sm font-medium">시작일</label>
          <Input
            aria-label="시작일"
            type="date"
            value={since}
            onInput={(e) => updateRange({ since: e.currentTarget.value, until })}
            onChange={(e) => updateRange({ since: e.target.value, until })}
            className="w-40"
          />
        </div>
        <div>
          <label className="block text-sm font-medium">종료일</label>
          <Input
            aria-label="종료일"
            type="date"
            value={until}
            onInput={(e) => updateRange({ since, until: e.currentTarget.value })}
            onChange={(e) => updateRange({ since, until: e.target.value })}
            className="w-40"
          />
        </div>
        <Button onClick={() => loadReports()} disabled={loading}>
          {loading ? "조회 중…" : "조회"}
        </Button>
        <Button
          variant="default"
          onClick={startDownload}
          disabled={running || selectedIds.size === 0}
        >
          {running ? "다운로드 중…" : `다운로드${selectedIds.size ? ` (${selectedIds.size})` : ""}`}
        </Button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {/* 진행률 */}
      {job && (
        <div className="rounded-md border p-3 text-sm">
          <div className="mb-1 flex justify-between">
            <span>
              다운로드 {job.status === "done" ? "완료" : job.status === "error" ? "오류" : "진행 중"}
              {" — "}
              받음 {job.downloaded} · 스킵 {job.skipped} · 실패 {job.failed} / 총 {job.total}
            </span>
            <span>{pct}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded bg-muted">
            <div className="h-full bg-ring transition-all" style={{ width: `${pct}%` }} />
          </div>
          {job.error && <p className="mt-1 text-destructive">{job.error}</p>}
        </div>
      )}

      {/* 목록 */}
      {data && (
        <div>
          <p className="mb-2 text-sm text-muted-foreground">
            {data.name} ({data.code}) — 총 {data.total}건, 이미 받음 {data.already}건
            {selectedIds.size > 0 && ` · ${selectedIds.size}건 선택됨`}
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <Checkbox
                    checked={allChecked}
                    indeterminate={someChecked}
                    disabled={rowCount === 0}
                    onCheckedChange={(v) => toggleAll(v === true)}
                    aria-label="전체 선택"
                  />
                </TableHead>
                <TableHead>날짜</TableHead>
                <TableHead>증권사</TableHead>
                <TableHead>제목</TableHead>
                <TableHead className="text-right">상태</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.reports.map((r, i) => (
                <TableRow key={`${r.researchId}-${i}`}>
                  <TableCell>
                    <Checkbox
                      checked={selectedIds.has(r.researchId)}
                      onCheckedChange={(v) => toggleOne(r.researchId, v === true)}
                      aria-label={`${r.brokerName} ${r.writeDate} 선택`}
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{r.writeDate}</TableCell>
                  <TableCell className="whitespace-nowrap">{r.brokerName}</TableCell>
                  <TableCell>{r.title}</TableCell>
                  <TableCell className="text-right">
                    {r.downloaded ? (
                      <span className="inline-flex items-center gap-2">
                        <span className="rounded bg-secondary px-2 py-0.5 text-xs">받음</span>
                        <a
                          href={pdfHref(data.code, data.name, r)}
                          target="_blank"
                          rel="noopener"
                          className="text-sm text-fin-gold underline-offset-2 hover:underline"
                        >
                          보기
                        </a>
                      </span>
                    ) : (
                      <span className="rounded border px-2 py-0.5 text-xs">미다운</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
