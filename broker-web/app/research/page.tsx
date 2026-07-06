"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { XIcon } from "lucide-react";
import { apiBase } from "@/lib/api-base";
import { initialRange, loadSavedRange, saveRange } from "@/lib/research-prefs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Candidate = { code: string; name: string };
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

export default function ResearchPage() {
  const [q, setQ] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  // SSR/CSR 일치용: 초기값은 저장소 안 읽음(로컬 저장값은 mount 후 effect에서 적용)
  const [dateRange, setDateRange] = useState(() =>
    initialRange(new Date(), null)
  );
  const [data, setData] = useState<ReportsResp | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLUListElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const since = dateRange.since;
  const until = dateRange.until;

  // 자동완성 (디바운스 250ms)
  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (selected || !q.trim()) {
      setCandidates([]);
      setActiveIndex(-1);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await fetch(
          `${apiBase()}/research/search?q=${encodeURIComponent(q.trim())}`
        );
        const next: Candidate[] = r.ok ? await r.json() : [];
        setCandidates(next);
        setActiveIndex(next.length > 0 ? 0 : -1);
      } catch {
        setCandidates([]);
        setActiveIndex(-1);
      }
    }, 250);
    return () => clearTimeout(debounceRef.current);
  }, [q, selected]);

  function pick(c: Candidate) {
    setSelected(c);
    setQ("");
    setCandidates([]);
    setActiveIndex(-1);
  }

  function clearSelection() {
    setSelected(null);
    setQ("");
    setData(null);
    setSelectedIds(new Set());
    setJob(null);
    setCandidates([]);
    setActiveIndex(-1);
  }

  function updateRange(next: { since: string; until: string }) {
    setDateRange(next);
    saveRange(next);
  }

  function updateSince(value: string) {
    updateRange({ since: value, until: dateRange.until });
  }

  function updateUntil(value: string) {
    updateRange({ since: dateRange.since, until: value });
  }

  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (candidates.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, candidates.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      pick(candidates[activeIndex]);
    } else if (e.key === "Escape") {
      setCandidates([]);
      setActiveIndex(-1);
    }
  }

  const loadReports = useCallback(async (opts?: { resetSelection?: boolean }) => {
    if (!selected) return;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ name: selected.name });
      if (since) params.set("since", since);
      if (until) params.set("until", until);
      const r = await fetch(
        `${apiBase()}/research/stock/${selected.code}/reports?${params}`
      );
      if (!r.ok) throw new Error(`조회 실패 (${r.status})`);
      const d: ReportsResp = await r.json();
      setData(d);
      if (opts?.resetSelection ?? true) {
        // 기본 선택: 아직 안 받은 항목만
        setSelectedIds(
          new Set(d.reports.filter((x) => !x.downloaded).map((x) => x.researchId))
        );
      } else {
        // 다운로드 후 refresh: 기존 선택 유지, 이번에 받아진 항목만 제거
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
  }, [selected, since, until]);

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
          loadReports({ resetSelection: false }); // 완료 후 다운로드 여부 갱신, 선택은 유지
        }
      } catch {
        clearInterval(pollRef.current);
      }
    }, 1500);
  }

  async function startDownload() {
    if (!selected) return;
    if (selectedIds.size === 0) {
      setError("다운로드할 리포트를 선택해줘");
      return;
    }
    setError("");
    try {
      const r = await fetch(
        `${apiBase()}/research/stock/${selected.code}/download`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: selected.name,
            since: since || null,
            until: until || null,
            researchIds: Array.from(selectedIds),
          }),
        }
      );
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

  // mount 후 저장값 적용(초기 렌더는 저장소 안 읽어 hydration 일치 보장)
  // setTimeout(0): set-state-in-effect 룰 회피 + 커밋 이후 지연 적용
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const saved = loadSavedRange();
      if (saved?.since && saved?.until) {
        setDateRange({ since: saved.since, until: saved.until });
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(
    () => () => {
      clearInterval(pollRef.current);
      clearTimeout(debounceRef.current);
    },
    []
  );

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        !inputRef.current?.contains(e.target as Node)
      ) {
        setCandidates([]);
        setActiveIndex(-1);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const done = job ? job.downloaded + job.skipped + job.failed : 0;
  const pct = job && job.total ? Math.round((done / job.total) * 100) : 0;
  const running = job?.status === "running";
  const rowCount = data?.reports.length ?? 0;
  const allChecked = rowCount > 0 && selectedIds.size === rowCount;
  const someChecked = selectedIds.size > 0 && !allChecked;

  return (
    <div className="mx-auto max-w-4xl p-6 space-y-6">
      <h1 className="text-2xl font-bold">증권사 종목 리포트</h1>

      {/* 검색 + 기간 */}
      <div className="space-y-3">
        <div className="relative">
          <label className="text-sm font-medium">종목 (코드 또는 종목명)</label>
          {selected ? (
            <div className="mt-1">
              <Badge variant="outline" className="h-8 gap-2 px-3 text-sm">
                <span>{selected.name}</span>
                <span className="text-muted-foreground">{selected.code}</span>
                <button
                  type="button"
                  aria-label="선택 해제"
                  className="rounded-sm p-0.5 hover:bg-muted"
                  onClick={clearSelection}
                >
                  <XIcon className="size-3.5" />
                </button>
              </Badge>
            </div>
          ) : (
            <Input
              ref={inputRef}
              value={q}
              placeholder="예: 삼성전자 또는 005930"
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={handleSearchKeyDown}
            />
          )}
          {candidates.length > 0 && (
            <ul
              ref={dropdownRef}
              className="absolute z-10 mt-1 w-full max-h-64 overflow-auto rounded-md border bg-popover text-popover-foreground shadow-md"
            >
              {candidates.map((c, i) => (
                <li
                  key={c.code}
                  className="cursor-pointer px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground data-[active=true]:bg-accent data-[active=true]:text-accent-foreground"
                  onMouseEnter={() => setActiveIndex(i)}
                  onMouseDown={() => pick(c)}
                  data-active={i === activeIndex}
                >
                  {c.name}{" "}
                  <span className="text-muted-foreground">{c.code}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-sm font-medium">시작일</label>
            <Input
              aria-label="시작일"
              type="date"
              value={since}
              onInput={(e) => updateSince(e.currentTarget.value)}
              onChange={(e) => updateSince(e.target.value)}
              className="w-40"
            />
          </div>
          <div>
            <label className="block text-sm font-medium">종료일</label>
            <Input
              aria-label="종료일"
              type="date"
              value={until}
              onInput={(e) => updateUntil(e.currentTarget.value)}
              onChange={(e) => updateUntil(e.target.value)}
              className="w-40"
            />
          </div>
          <Button onClick={() => loadReports()} disabled={!selected || loading}>
            {loading ? "조회 중…" : "조회"}
          </Button>
          <Button
            variant="default"
            onClick={startDownload}
            disabled={!selected || running || selectedIds.size === 0}
          >
            {running
              ? "다운로드 중…"
              : `다운로드${selectedIds.size ? ` (${selectedIds.size})` : ""}`}
          </Button>
        </div>
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
            <div
              className="h-full bg-ring transition-all"
              style={{ width: `${pct}%` }}
            />
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
                        <Badge variant="secondary">받음</Badge>
                        <a
                          href={pdfHref(data.code, data.name, r)}
                          target="_blank"
                          rel="noopener"
                          className="text-sm text-primary underline-offset-2 hover:underline"
                        >
                          보기
                        </a>
                      </span>
                    ) : (
                      <Badge variant="outline">미다운</Badge>
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
