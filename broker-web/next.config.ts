import type { NextConfig } from "next";
import path from "node:path";

// 원격(LAN IP) 브라우저에서 dev 접근 시 Next이 /_next/*(HMR WS 포함)를 cross-origin으로
// 차단 → hydration 멈춤(빈 데이터·끊김). 허용할 origin(들)을 DEV_ORIGIN env로 주입.
// 머신/네트워크별 IP라 코드에 박지 않고 .env.local(gitignore)에 둔다. 예: DEV_ORIGIN=172.30.1.94
// 미설정 시 빈 배열 → localhost 접근만(원래 설계), 영향 없음.
const devOrigins = process.env.DEV_ORIGIN?.split(",").map((s) => s.trim()).filter(Boolean) ?? [];

const nextConfig: NextConfig = {
  // dev와 prod를 다른 빌드 출력 dir로 분리해 동시 실행 시 `.next` 충돌 방지.
  // 미설정(dev)=기본 `.next`, prod 배치는 NEXT_DIST_DIR=.next-prod 로 build/start.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  allowedDevOrigins: devOrigins,
  // 상위 디렉토리에 두 번째 package-lock.json이 있어 Next이 워크스페이스 root를
  // repo 루트로 오추론 → dev 청크 경로 틀어짐(illegal path)·HMR 끊김. root를 이 앱으로 고정.
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
