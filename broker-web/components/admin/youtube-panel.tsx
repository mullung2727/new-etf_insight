"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import type { SummaryMode } from "@/lib/youtube-channels";

type Channel = {
  key: string;
  url: string;
  label: string;
  handle?: string;
  discovery: boolean;
  summaryMode: SummaryMode;
  summaryHint: string;
};
type Data = { active: Channel[]; deleted: Channel[] };
type RowEdit = {
  label: string;
  discovery: boolean;
  summaryMode: SummaryMode;
  summaryHint: string;
};

export function YoutubePanel() {
  const [data, setData] = useState<Data>({ active: [], deleted: [] });
  const [edit, setEdit] = useState<Record<string, RowEdit>>({});
  const [addUrl, setAddUrl] = useState("");
  const [addLabel, setAddLabel] = useState("");
  const [addDiscovery, setAddDiscovery] = useState(false);
  const [addSummaryMode, setAddSummaryMode] = useState<SummaryMode>("manual");
  const [error, setError] = useState<string | null>(null);
  const [showDeleted, setShowDeleted] = useState(false);

  const reload = useCallback(async () => {
    const res = await fetch("/api/youtube-channels");
    setData(await res.json());
    setEdit({});
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function send(method: string, body: object) {
    setError(null);
    const res = await fetch("/api/youtube-channels", {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (!res.ok) {
      setError(json.error ?? `오류 ${res.status}`);
      return false;
    }
    setData(json);
    setEdit({});
    return true;
  }

  const rowOf = (c: Channel): RowEdit =>
    edit[c.key] ?? {
      label: c.label,
      discovery: c.discovery,
      summaryMode: c.summaryMode ?? "manual",
      summaryHint: c.summaryHint ?? "",
    };
  const dirty = (c: Channel) => {
    const r = edit[c.key];
    return (
      !!r &&
      (r.label !== c.label ||
        r.discovery !== c.discovery ||
        r.summaryMode !== (c.summaryMode ?? "manual") ||
        r.summaryHint !== (c.summaryHint ?? ""))
    );
  };
  const patchRow = (c: Channel, p: Partial<RowEdit>) =>
    setEdit((e) => ({ ...e, [c.key]: { ...rowOf(c), ...p } }));

  const saveAll = async () => {
    const payloads = data.active.filter(dirty).map((c) => ({ key: c.key, ...rowOf(c) }));
    setError(null);
    for (const p of payloads) {
      const res = await fetch("/api/youtube-channels", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(p),
      });
      if (!res.ok) {
        setError((await res.json()).error ?? `오류 ${res.status}`);
        break;
      }
    }
    await reload();
  };
  const del = (c: Channel) => send("DELETE", { key: c.key });
  const restore = (c: Channel) => send("POST", { action: "restore", key: c.key });

  const add = async () => {
    if (!addUrl.trim()) return;
    const ok = await send("POST", {
      action: "add",
      url: addUrl,
      label: addLabel,
      discovery: addDiscovery,
      summaryMode: addSummaryMode,
    });
    if (ok) {
      setAddUrl("");
      setAddLabel("");
      setAddDiscovery(false);
      setAddSummaryMode("manual");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-base font-semibold">유튜브 채널 관리</h1>
          <p className="text-sm text-muted-foreground">
            수집 대상 목록. 요약 모드: 자동=스케줄 포함, 수동=자동 안 함(수동은 전 채널 가능).
          </p>
        </div>
        <Button onClick={saveAll} disabled={!data.active.some(dirty)}>
          저장
        </Button>
      </div>

      {error && (
        <p className="rounded-md border border-destructive bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="rounded-md border border-border bg-muted/40 px-4 py-3 text-sm">
        <p className="mb-1 font-medium">종목탐색 · 요약모드</p>
        <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
          <li>
            <b>종목탐색 체크</b> → 요약 후 종목 후보 추출 대상.
          </li>
          <li>
            <b>요약 자동</b> → 일배치가 미요약 자동 처리. <b>수동</b> → 자동 요약 안 함.
          </li>
          <li>
            수동 요약 버튼은 <b>모든 채널</b>에서 사용 가능.
          </li>
          <li>
            <b>요약 특성</b> → 요약 프롬프트에 그대로 주입. 비우면 범용 원칙만 적용.
          </li>
        </ul>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">채널이름</th>
            <th className="pb-2 font-medium">URL</th>
            <th className="pb-2 font-medium">ID</th>
            <th className="pb-2 text-center font-medium">종목탐색</th>
            <th className="pb-2 text-center font-medium">요약</th>
            <th className="pb-2" />
          </tr>
        </thead>
        <tbody>
          {data.active.map((c) => {
            const r = rowOf(c);
            return (
              <Fragment key={c.key}>
                <tr className="border-b border-border/50">
                <td className="py-2 pr-3">
                  <Input
                    value={r.label}
                    onChange={(e) => patchRow(c, { label: e.target.value })}
                    placeholder={c.key}
                    className="h-8"
                  />
                </td>
                <td className="py-2 pr-3">
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs text-fin-gold hover:underline"
                  >
                    {c.url.replace("https://", "")}
                  </a>
                </td>
                <td className="py-2 pr-3">
                  <span className="font-mono text-[11px] text-muted-foreground">{c.key}</span>
                </td>
                <td className="py-2 text-center">
                  <Checkbox
                    checked={r.discovery}
                    onCheckedChange={(v) => patchRow(c, { discovery: !!v })}
                    aria-label="종목탐색 소스"
                  />
                </td>
                <td className="py-2 text-center">
                  <select
                    value={r.summaryMode}
                    onChange={(e) =>
                      patchRow(c, { summaryMode: e.target.value as SummaryMode })
                    }
                    className="h-8 rounded-md border border-border bg-background px-1 text-xs"
                    aria-label="요약 모드"
                  >
                    <option value="manual">수동</option>
                    <option value="auto">자동</option>
                  </select>
                </td>
                <td className="py-2 text-right whitespace-nowrap">
                  <Button size="xs" variant="destructive" onClick={() => del(c)}>
                    삭제
                  </Button>
                </td>
                </tr>
                <tr key={`${c.key}-hint`} className="border-b border-border/50">
                  <td colSpan={6} className="pb-3">
                    <textarea
                      value={r.summaryHint}
                      onChange={(e) => patchRow(c, { summaryHint: e.target.value })}
                      rows={2}
                      placeholder="요약 특성(선택) — 예: 하루치 뉴스에서 독립 주제 3개를 다룸. 주제별로 분리해 요약할 것."
                      className="w-full resize-y rounded-md border border-border bg-background px-2 py-1.5 text-xs leading-relaxed"
                      aria-label={`${c.label} 요약 특성`}
                    />
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>

      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-3">
        <Input
          value={addUrl}
          onChange={(e) => setAddUrl(e.target.value)}
          placeholder="채널 URL · @handle · UC…"
          className="h-8 min-w-64 flex-1"
        />
        <Input
          value={addLabel}
          onChange={(e) => setAddLabel(e.target.value)}
          placeholder="이름(선택)"
          className="h-8 w-40"
        />
        <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Checkbox checked={addDiscovery} onCheckedChange={(v) => setAddDiscovery(!!v)} />
          종목탐색
        </label>
        <select
          value={addSummaryMode}
          onChange={(e) => setAddSummaryMode(e.target.value as SummaryMode)}
          className="h-8 rounded-md border border-border bg-background px-1 text-xs"
        >
          <option value="manual">요약 수동</option>
          <option value="auto">요약 자동</option>
        </select>
        <Button size="sm" onClick={add}>
          + 채널 추가
        </Button>
      </div>

      {data.deleted.length > 0 && (
        <div className="space-y-2">
          <button
            onClick={() => setShowDeleted((s) => !s)}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            {showDeleted ? "▾" : "▸"} 삭제된 채널 ({data.deleted.length})
          </button>
          {showDeleted && (
            <div className="space-y-1">
              {data.deleted.map((c) => (
                <div
                  key={c.key}
                  className="flex items-center justify-between rounded-md border border-border/50 px-3 py-1.5 text-sm"
                >
                  <span className="flex items-center gap-2">
                    {c.label}
                    <span className="font-mono text-xs text-muted-foreground">{c.key}</span>
                    {c.discovery && <Badge variant="secondary">종목탐색</Badge>}
                    <Badge variant="outline">
                      {(c.summaryMode ?? "manual") === "auto" ? "요약자동" : "요약수동"}
                    </Badge>
                  </span>
                  <Button size="xs" variant="outline" onClick={() => restore(c)}>
                    복구
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
