import type { NextConfig } from "next";

// 원격(LAN IP) 브라우저에서 dev 접근 시 Next이 /_next/*(HMR WS 포함)를 cross-origin으로
// 차단 → hydration 멈춤(빈 데이터·끊김). 허용할 origin(들)을 DEV_ORIGIN env로 주입.
// 머신/네트워크별 IP라 코드에 박지 않고 .env.local(gitignore)에 둔다. 예: DEV_ORIGIN=172.30.1.94
// 미설정 시 빈 배열 → localhost 접근만(원래 설계), 영향 없음.
const devOrigins = process.env.DEV_ORIGIN?.split(",").map((s) => s.trim()).filter(Boolean) ?? [];

const nextConfig: NextConfig = {
  allowedDevOrigins: devOrigins,
};

export default nextConfig;
