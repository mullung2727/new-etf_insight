import { test, expect } from "@playwright/test";
import { toDate, sortTimeline, type TimelineEvent } from "@/lib/timeline";

test("toDate: ISO datetime → YYYY-MM-DD", () => {
  expect(toDate("2026-07-06T09:30:00+09:00")).toBe("2026-07-06");
  expect(toDate("2026-07-06")).toBe("2026-07-06");
});

test("sortTimeline: newest first, across mixed sources same day", () => {
  const events: TimelineEvent[] = [
    { date: "2026-07-05", kind: "mention", title: "언급" },
    { date: "2026-07-07", kind: "trade", title: "매수" },
    { date: "2026-07-06", kind: "mention", title: "언급2" },
    { date: "2026-07-07", kind: "mention", title: "같은날 언급" },
  ];
  const sorted = sortTimeline(events);
  expect(sorted.map((e) => e.date)).toEqual([
    "2026-07-07", "2026-07-07", "2026-07-06", "2026-07-05",
  ]);
});

test("sortTimeline: does not mutate input", () => {
  const events: TimelineEvent[] = [
    { date: "2026-07-05", kind: "mention", title: "a" },
    { date: "2026-07-07", kind: "trade", title: "b" },
  ];
  sortTimeline(events);
  expect(events[0].date).toBe("2026-07-05"); // 원본 순서 유지
});
