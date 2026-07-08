// 개요 탭 이벤트 타임라인. 서로 다른 소스(텔레그램 언급/노트 매매)를 한 날짜축에 병합.
export type TimelineKind = "mention" | "trade" | "score";

export interface TimelineEvent {
  date: string; // YYYY-MM-DD
  kind: TimelineKind;
  title: string;
  detail?: string;
}

// ISO datetime(노트 executed_at) 또는 date(텔레그램 date_kst) → YYYY-MM-DD.
export function toDate(ts: string): string {
  return ts.slice(0, 10);
}

// 최신순. 같은 날짜는 입력 순서 유지(안정 정렬). 원본 불변.
export function sortTimeline(events: TimelineEvent[]): TimelineEvent[] {
  return [...events].sort((a, b) => b.date.localeCompare(a.date));
}
