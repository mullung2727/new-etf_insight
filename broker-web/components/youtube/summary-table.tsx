"use client";

import { Fragment, useState } from "react";
import type { YoutubeVideoSummary } from "@/lib/queries";

export function YoutubeSummaryTable({ items }: { items: YoutubeVideoSummary[] }) {
  const [openKey, setOpenKey] = useState<string | null>(null);

  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">요약된 영상이 없습니다.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full table-fixed text-sm">
        <colgroup>
          <col className="w-8" />
          <col className="w-28" />
          <col className="w-32" />
          <col />
          <col className="w-16" />
        </colgroup>
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
            <th className="px-2 py-2" />
            <th className="px-3 py-2 font-medium">날짜</th>
            <th className="px-3 py-2 font-medium">채널</th>
            <th className="px-3 py-2 font-medium">제목</th>
            <th className="px-3 py-2 font-medium">링크</th>
          </tr>
        </thead>
        <tbody>
          {items.map((v) => {
            const key = `${v.channel_id}-${v.video_id}`;
            const open = openKey === key;
            const title = (v.title && v.title.trim()) || v.video_id;
            const url =
              (v.url && v.url.trim()) ||
              `https://www.youtube.com/watch?v=${v.video_id}`;
            const channel = (v.channel_label && v.channel_label.trim()) || v.channel_id;

            return (
              <Fragment key={key}>
                <tr
                  className="cursor-pointer border-b border-border/60 hover:bg-muted/30"
                  onClick={() => setOpenKey(open ? null : key)}
                >
                  <td className="px-2 py-2.5 text-center text-muted-foreground">
                    {open ? "▾" : "▸"}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                    {v.date_kst}
                  </td>
                  <td className="px-3 py-2.5 text-sm text-foreground" title={channel}>
                    <span className="line-clamp-2 break-words">{channel}</span>
                  </td>
                  <td className="px-3 py-2.5 font-medium text-foreground" title={title}>
                    <span className="line-clamp-2 break-words">{title}</span>
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center rounded border border-fin-gold/50 bg-fin-gold/10 px-2 py-1 text-xs font-medium text-fin-gold underline-offset-2 hover:bg-fin-gold/20 hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      열기
                    </a>
                  </td>
                </tr>
                {open && (
                  <tr className="border-b border-border/60 bg-muted/20">
                    <td colSpan={5} className="space-y-2 px-4 py-3 pl-10">
                      {v.headline && (
                        <p className="text-sm text-foreground/90">{v.headline}</p>
                      )}
                      {v.issues?.length > 0 && (
                        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                          {v.issues.map((iss, i) => (
                            <li key={i}>
                              <span className="text-foreground/80">{iss.title}</span>
                              {iss.summary ? ` — ${iss.summary}` : ""}
                            </li>
                          ))}
                        </ul>
                      )}
                      {v.bullets?.length > 0 && (
                        <ul className="list-disc pl-5 text-xs text-muted-foreground">
                          {v.bullets.map((b, i) => (
                            <li key={i}>{b}</li>
                          ))}
                        </ul>
                      )}
                      {v.risk_or_caveat && (
                        <p className="text-xs text-amber-500/90">주의: {v.risk_or_caveat}</p>
                      )}
                      <div className="flex flex-wrap gap-3 pt-1 text-xs text-muted-foreground">
                        <span className="font-mono">{v.video_id}</span>
                        <a
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          className="break-all text-fin-gold hover:underline"
                        >
                          {url}
                        </a>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
