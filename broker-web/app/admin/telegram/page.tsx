"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";

type Channel = { key: string; url: string; label: string; discovery: boolean };
type Data = { active: Channel[]; deleted: Channel[] };

export default function TelegramAdminPage() {
  const [data, setData] = useState<Data>({ active: [], deleted: [] });
  const [edit, setEdit] = useState<Record<string, { label: string; discovery: boolean }>>({});
  const [addUrl, setAddUrl] = useState("");
  const [addLabel, setAddLabel] = useState("");
  const [addDiscovery, setAddDiscovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDeleted, setShowDeleted] = useState(false);

  const reload = useCallback(async () => {
    const res = await fetch("/api/telegram-channels");
    setData(await res.json());
    setEdit({});
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function send(method: string, body: object) {
    setError(null);
    const res = await fetch("/api/telegram-channels", {
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

  const rowOf = (c: Channel) => edit[c.key] ?? { label: c.label, discovery: c.discovery };
  const dirty = (c: Channel) => {
    const r = edit[c.key];
    return !!r && (r.label !== c.label || r.discovery !== c.discovery);
  };
  const patchRow = (c: Channel, p: Partial<{ label: string; discovery: boolean }>) =>
    setEdit((e) => ({ ...e, [c.key]: { ...rowOf(c), ...p } }));

  const saveAll = async () => {
    // send()가 성공 시 edit을 비우므로 루프 전에 payload 스냅샷
    const payloads = data.active.filter(dirty).map((c) => ({ key: c.key, ...rowOf(c) }));
    setError(null);
    for (const p of payloads) {
      const res = await fetch("/api/telegram-channels", {
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
    });
    if (ok) {
      setAddUrl("");
      setAddLabel("");
      setAddDiscovery(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-base font-semibold">텔레그램 채널 관리</h1>
          <p className="text-sm text-muted-foreground">
            수집 대상 채널 목록. <b>종목탐색 소스</b>(discovery_source)로 표시된 채널은 원문에서
            종목 후보를 추출하는 데 쓰입니다.
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

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">채널이름 (참고용)</th>
            <th className="pb-2 font-medium">URL</th>
            <th className="pb-2 text-center font-medium">종목탐색 소스</th>
            <th className="pb-2" />
          </tr>
        </thead>
        <tbody>
          {data.active.map((c) => {
            const r = rowOf(c);
            return (
              <tr key={c.key} className="border-b border-border/50">
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
                    className="font-mono text-xs text-primary hover:underline"
                  >
                    {c.url.replace("https://", "")}
                  </a>
                </td>
                <td className="py-2 text-center">
                  <Checkbox
                    checked={r.discovery}
                    onCheckedChange={(v) => patchRow(c, { discovery: !!v })}
                    aria-label="종목탐색 소스"
                  />
                </td>
                <td className="py-2 text-right whitespace-nowrap">
                  <Button size="xs" variant="destructive" onClick={() => del(c)}>
                    삭제
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* 추가 */}
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-3">
        <Input
          value={addUrl}
          onChange={(e) => setAddUrl(e.target.value)}
          placeholder="채널 URL 붙여넣기 (예: https://t.me/s/getfeed)"
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
        <Button size="sm" onClick={add}>
          + 채널 추가
        </Button>
      </div>

      {/* 삭제된 채널 */}
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
                    <span className="font-mono text-xs text-muted-foreground">
                      {c.url.replace("https://", "")}
                    </span>
                    {c.discovery && <Badge variant="secondary">종목탐색</Badge>}
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
