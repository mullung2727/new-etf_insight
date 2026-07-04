export type ResearchDateRange = {
  since: string;
  until: string;
};

const STORAGE_KEY = "research.dateRange";

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function formatLocalDate(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function todayISO(now: Date): string {
  return formatLocalDate(now);
}

export function monthsAgoISO(now: Date, months: number): string {
  const targetFirst = new Date(now.getFullYear(), now.getMonth() - months, 1);
  const targetLast = new Date(
    targetFirst.getFullYear(),
    targetFirst.getMonth() + 1,
    0
  );
  const day = Math.min(now.getDate(), targetLast.getDate());

  return formatLocalDate(
    new Date(targetFirst.getFullYear(), targetFirst.getMonth(), day)
  );
}

export function initialRange(
  now: Date,
  saved: Partial<ResearchDateRange> | null
): ResearchDateRange {
  if (saved?.since && saved?.until) {
    return { since: saved.since, until: saved.until };
  }

  return {
    since: monthsAgoISO(now, 3),
    until: todayISO(now),
  };
}

export function loadSavedRange(): Partial<ResearchDateRange> | null {
  try {
    if (typeof localStorage === "undefined") return null;
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveRange(range: ResearchDateRange): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(range));
  } catch {
    // Storage can be unavailable in private or restricted contexts.
  }
}
