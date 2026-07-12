import path from "node:path";
import fs from "node:fs/promises";

// youtube_channels.json 관리. 최상위 key = channel_id (UC…).
// 소프트delete → 사이드카. 텔레그램 lib 대칭.
// 스펙: docs/youtube_tech.md §3.4

export type SummaryMode = "auto" | "manual";

export type Channel = {
  key: string; // channel_id UC…
  url: string; // source_url
  label: string;
  handle?: string;
  discovery: boolean;
  /** auto: 스케줄 요약 대상. manual: 자동 요약 안 함. 수동은 항상 가능. */
  summaryMode: SummaryMode;
};

type Entry = {
  source_url: string;
  handle?: string;
  feed_role?: string;
  label?: string;
  summary_mode?: string;
};
type FileMap = Record<string, Entry>;

const CHANNEL_ID_RE = /^UC[a-zA-Z0-9_-]{22}$/;
const CHANNEL_PATH_RE = /\/channel\/(UC[a-zA-Z0-9_-]{22})(?:\/|$|\?)/i;
const LINK_CANONICAL_RE =
  /<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)["']/i;
const LINK_CANONICAL_RE2 =
  /<link[^>]+href=["']([^"']+)["'][^>]+rel=["']canonical["']/i;
const OG_URL_RE =
  /<meta[^>]+property=["']og:url["'][^>]+content=["']([^"']+)["']/i;
const OG_URL_RE2 =
  /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:url["']/i;
const CHANNEL_ID_JSON_RE = /"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"/;

function repoRoot() {
  return path.join(process.cwd(), "..");
}
export const ACTIVE_PATH = () =>
  path.join(repoRoot(), "etl", "scripts", "youtube_channels.json");
export const DELETED_PATH = () =>
  path.join(repoRoot(), "etl", "scripts", "youtube_channels.deleted.json");

function ucFromUrl(url: string): string | null {
  const m = url.match(CHANNEL_PATH_RE);
  if (!m) return null;
  return CHANNEL_ID_RE.test(m[1]) ? m[1] : null;
}

/** 로컬 UC 파싱만. throw Error */
export function extractChannelId(input: string): string {
  const s = input.trim();
  if (!s) throw new Error("빈 채널 입력");

  if (CHANNEL_ID_RE.test(s)) return s;

  const low = s.toLowerCase();
  if (low.includes("youtu.be/") || low.includes("/watch") || low.includes("/shorts/")) {
    throw new Error(`영상 URL은 채널이 아님: ${input}`);
  }

  if (s.startsWith("@") || low.includes("youtube.com/@")) {
    throw new Error(`@handle 은 resolve 필요: ${input}`);
  }

  const m = s.match(CHANNEL_PATH_RE);
  if (m && CHANNEL_ID_RE.test(m[1])) return m[1];

  throw new Error(`channel_id 파싱 실패: ${input}`);
}

function extractFromHtml(html: string): string {
  for (const re of [LINK_CANONICAL_RE, LINK_CANONICAL_RE2, OG_URL_RE, OG_URL_RE2]) {
    const m = html.match(re);
    if (m) {
      const cid = ucFromUrl(m[1]);
      if (cid) return cid;
    }
  }

  const headEnd = html.toLowerCase().indexOf("</head>");
  const headChunk = headEnd >= 0 ? html.slice(0, headEnd + 7) : html.slice(0, 8000);
  const headMatch = headChunk.match(CHANNEL_ID_JSON_RE);
  if (headMatch && CHANNEL_ID_RE.test(headMatch[1])) return headMatch[1];

  const full = html.match(CHANNEL_ID_JSON_RE);
  if (full && CHANNEL_ID_RE.test(full[1])) return full[1];

  throw new Error("HTML에서 channel_id를 찾지 못함");
}

function normalizeHandleUrl(raw: string): string {
  const s = raw.trim();
  if (s.startsWith("@")) return `https://www.youtube.com/${s}`;
  if (s.startsWith("http://") || s.startsWith("https://")) return s;
  if (s.startsWith("youtube.com/") || s.startsWith("www.youtube.com/")) {
    return `https://${s}`;
  }
  throw new Error(`resolve 할 수 없는 입력: ${raw}`);
}

/** @handle 이면 fetch 채널 HTML → UC. 테스트는 mock fetch. */
export async function resolveChannelId(
  input: string,
  fetchImpl: typeof fetch = fetch
): Promise<string> {
  try {
    return extractChannelId(input);
  } catch {
    // fall through to network resolve for @handle
  }

  const s = input.trim();
  if (!s) throw new Error("빈 채널 입력");

  const low = s.toLowerCase();
  if (low.includes("youtu.be/") || low.includes("/watch") || low.includes("/shorts/")) {
    throw new Error(`영상 URL은 채널이 아님: ${input}`);
  }

  const url = normalizeHandleUrl(s);
  let res: Response;
  try {
    res = await fetchImpl(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      },
    });
  } catch {
    throw new Error(`채널 페이지 조회 실패: ${input}`);
  }
  if (!res.ok) throw new Error(`채널 페이지 조회 실패: ${input}`);
  const html = await res.text();
  return extractFromHtml(html);
}

function parseSummaryMode(raw: string | undefined): SummaryMode {
  return raw === "auto" ? "auto" : "manual";
}

export function entryToChannel(key: string, e: Entry): Channel {
  return {
    key,
    url: e.source_url || `https://www.youtube.com/channel/${key}`,
    label: e.label || e.handle || key,
    handle: e.handle,
    discovery: e.feed_role === "discovery_source",
    summaryMode: parseSummaryMode(e.summary_mode),
  };
}

export function channelToEntry(c: {
  key: string;
  label?: string;
  handle?: string;
  discovery: boolean;
  summaryMode?: SummaryMode;
}): Entry {
  const e: Entry = {
    source_url: `https://www.youtube.com/channel/${c.key}`,
  };
  if (c.discovery) e.feed_role = "discovery_source";
  if (c.handle) e.handle = c.handle;
  if (c.label && c.label !== c.key) e.label = c.label;
  const mode = c.summaryMode === "auto" ? "auto" : "manual";
  e.summary_mode = mode;
  return e;
}

async function readMap(p: string): Promise<FileMap> {
  try {
    return JSON.parse(await fs.readFile(p, "utf-8"));
  } catch {
    return {};
  }
}
async function writeMap(p: string, m: FileMap) {
  await fs.writeFile(p, JSON.stringify(m, null, 2) + "\n", "utf-8");
}

export async function listChannels() {
  const [active, deleted] = await Promise.all([
    readMap(ACTIVE_PATH()),
    readMap(DELETED_PATH()),
  ]);
  return {
    active: Object.entries(active).map(([k, e]) => entryToChannel(k, e)),
    deleted: Object.entries(deleted).map(([k, e]) => entryToChannel(k, e)),
  };
}

export async function addChannel(input: {
  url: string;
  label?: string;
  handle?: string;
  discovery: boolean;
  summaryMode?: SummaryMode;
}) {
  const key = await resolveChannelId(input.url);
  const [active, deleted] = await Promise.all([
    readMap(ACTIVE_PATH()),
    readMap(DELETED_PATH()),
  ]);
  if (active[key]) throw new Error(`이미 있는 채널: ${key}`);
  if (deleted[key]) throw new Error(`삭제목록에 있음. 복구하세요: ${key}`);
  active[key] = channelToEntry({
    key,
    label: input.label,
    handle: input.handle,
    discovery: input.discovery,
    summaryMode: input.summaryMode ?? "manual",
  });
  await writeMap(ACTIVE_PATH(), active);
  return key;
}

export async function editChannel(input: {
  key: string;
  label?: string;
  handle?: string;
  discovery: boolean;
  summaryMode?: SummaryMode;
}) {
  const active = await readMap(ACTIVE_PATH());
  if (!active[input.key]) throw new Error(`없는 채널: ${input.key}`);
  const prev = active[input.key];
  const prevMode = parseSummaryMode(prev.summary_mode);
  active[input.key] = channelToEntry({
    key: input.key,
    label: input.label,
    handle: input.handle ?? prev.handle,
    discovery: input.discovery,
    summaryMode: input.summaryMode ?? prevMode,
  });
  await writeMap(ACTIVE_PATH(), active);
}

export async function softDelete(key: string) {
  const [active, deleted] = await Promise.all([
    readMap(ACTIVE_PATH()),
    readMap(DELETED_PATH()),
  ]);
  if (!active[key]) throw new Error(`없는 채널: ${key}`);
  deleted[key] = active[key];
  delete active[key];
  await Promise.all([writeMap(ACTIVE_PATH(), active), writeMap(DELETED_PATH(), deleted)]);
}

export async function restoreChannel(key: string) {
  const [active, deleted] = await Promise.all([
    readMap(ACTIVE_PATH()),
    readMap(DELETED_PATH()),
  ]);
  if (!deleted[key]) throw new Error(`삭제목록에 없음: ${key}`);
  active[key] = deleted[key];
  delete deleted[key];
  await Promise.all([writeMap(ACTIVE_PATH(), active), writeMap(DELETED_PATH(), deleted)]);
}
