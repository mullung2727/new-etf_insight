import path from "node:path";

// broker-web 밖(다른 앱 소유)에 있지만 손으로 관리하는 JSON 파일들.
// 파일은 각 앱의 리더가 상대경로로 하드코딩해 읽으므로 이동 금지 — 제자리 편집만.
// 클라이언트는 절대 경로를 보내지 않고 key만 보낸다(경로 탈출 차단).

export type ManagedFile = {
  key: string;
  label: string;
  relPath: string; // repo root 기준 상대경로
  description: string;
  risk: "safe" | "caution";
};

export const MANAGED_FILES: ManagedFile[] = [
  {
    key: "holding_aliases",
    label: "보유종목 별칭",
    relPath: "etl/data/holding_aliases.json",
    description:
      "종목명↔티커 별칭 매핑(예: 크래프톤→259960, KRAFTON). holding_identifier가 보유종목 식별에 사용.",
    risk: "safe",
  },
  {
    key: "telegram_channels",
    label: "텔레그램 채널",
    relPath: "etl/scripts/telegram_channels.json",
    description:
      "수집 대상 텔레그램 채널 목록과 feed_role(discovery_source 등). 채널 수집 배치가 읽는다.",
    risk: "safe",
  },
  {
    key: "cron_registry",
    label: "배치 레지스트리",
    relPath: "ops/batches/openclaw-cron.registry.json",
    description:
      "배치 잡 스케줄(cron/windowsTask) 레지스트리. jobs[] 구조가 깨지면 배치가 실패하니 주의.",
    risk: "caution",
  },
];

// repo root = broker-web 디렉터리의 부모. dev 서버·테스트 모두 cwd=broker-web.
export function repoRoot(): string {
  return path.join(process.cwd(), "..");
}

export function findManaged(key: string): ManagedFile | undefined {
  return MANAGED_FILES.find((f) => f.key === key);
}

export function resolveManaged(key: string): { file: ManagedFile; absPath: string } {
  const file = findManaged(key);
  if (!file) throw new Error(`unknown managed file key: ${key}`);
  return { file, absPath: path.join(repoRoot(), file.relPath) };
}
