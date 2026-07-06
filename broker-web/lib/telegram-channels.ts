import path from "node:path";
import fs from "node:fs/promises";

// telegram_channels.json 관리. 파일은 flat { slug: {source_url, feed_role?, label?} }.
// 리더(etl/scripts/telegram_channels.py)가 최상위 key 전부를 채널로 순회하므로
// 소프트delete 항목은 같은 파일에 못 둠 → 사이드카 파일에 보관.
// label 은 참고용 이름(리더가 안 읽음, .get/직접키 접근이라 무해).
// ponytail: 로컬 단일사용자 도구라 파일 락 없음. 다중 편집 생기면 그때.

export type Channel = {
  key: string; // t.me slug = 신원(불변)
  url: string; // https://t.me/s/<key>
  label: string; // 참고용 이름(없으면 key)
  discovery: boolean; // feed_role === "discovery_source"
};

type Entry = { source_url: string; feed_role?: string; label?: string };
type FileMap = Record<string, Entry>;

function repoRoot() {
  return path.join(process.cwd(), "..");
}
export const ACTIVE_PATH = () => path.join(repoRoot(), "etl", "scripts", "telegram_channels.json");
export const DELETED_PATH = () =>
  path.join(repoRoot(), "etl", "scripts", "telegram_channels.deleted.json");

// URL(https://t.me/s/foo, t.me/s/foo) 또는 bare slug → slug
export function extractSlug(input: string): string {
  const s = input.trim();
  const m = s.match(/t\.me\/s\/([A-Za-z0-9_]+)/);
  if (m) return m[1];
  if (/^[A-Za-z0-9_]+$/.test(s)) return s;
  throw new Error(`유효한 채널 URL/slug 아님: ${input}`);
}

export function entryToChannel(key: string, e: Entry): Channel {
  return {
    key,
    url: e.source_url || `https://t.me/s/${key}`,
    label: e.label || key,
    discovery: e.feed_role === "discovery_source",
  };
}

export function channelToEntry(c: { key: string; label?: string; discovery: boolean }): Entry {
  const e: Entry = { source_url: `https://t.me/s/${c.key}` };
  if (c.discovery) e.feed_role = "discovery_source";
  if (c.label && c.label !== c.key) e.label = c.label;
  return e;
}

async function readMap(p: string): Promise<FileMap> {
  try {
    return JSON.parse(await fs.readFile(p, "utf-8"));
  } catch {
    return {}; // 없는 사이드카 = 빈 목록
  }
}
async function writeMap(p: string, m: FileMap) {
  await fs.writeFile(p, JSON.stringify(m, null, 2) + "\n", "utf-8");
}

export async function listChannels() {
  const [active, deleted] = await Promise.all([readMap(ACTIVE_PATH()), readMap(DELETED_PATH())]);
  return {
    active: Object.entries(active).map(([k, e]) => entryToChannel(k, e)),
    deleted: Object.entries(deleted).map(([k, e]) => entryToChannel(k, e)),
  };
}

export async function addChannel(input: { url: string; label?: string; discovery: boolean }) {
  const key = extractSlug(input.url);
  const [active, deleted] = await Promise.all([readMap(ACTIVE_PATH()), readMap(DELETED_PATH())]);
  if (active[key]) throw new Error(`이미 있는 채널: ${key}`);
  if (deleted[key]) throw new Error(`삭제목록에 있음. 복구하세요: ${key}`);
  active[key] = channelToEntry({ key, label: input.label, discovery: input.discovery });
  await writeMap(ACTIVE_PATH(), active);
  return key;
}

export async function editChannel(input: { key: string; label?: string; discovery: boolean }) {
  const active = await readMap(ACTIVE_PATH());
  if (!active[input.key]) throw new Error(`없는 채널: ${input.key}`);
  active[input.key] = channelToEntry(input);
  await writeMap(ACTIVE_PATH(), active);
}

export async function softDelete(key: string) {
  const [active, deleted] = await Promise.all([readMap(ACTIVE_PATH()), readMap(DELETED_PATH())]);
  if (!active[key]) throw new Error(`없는 채널: ${key}`);
  deleted[key] = active[key];
  delete active[key];
  await Promise.all([writeMap(ACTIVE_PATH(), active), writeMap(DELETED_PATH(), deleted)]);
}

export async function restoreChannel(key: string) {
  const [active, deleted] = await Promise.all([readMap(ACTIVE_PATH()), readMap(DELETED_PATH())]);
  if (!deleted[key]) throw new Error(`삭제목록에 없음: ${key}`);
  active[key] = deleted[key];
  delete deleted[key];
  await Promise.all([writeMap(ACTIVE_PATH(), active), writeMap(DELETED_PATH(), deleted)]);
}
