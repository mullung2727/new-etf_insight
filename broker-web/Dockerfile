# broker-web — Next 16 / React 19 (dev image)
FROM node:22-slim

WORKDIR /app

# 의존성 레이어 (소스보다 먼저 → 캐시 활용)
COPY package.json package-lock.json ./
RUN npm ci

# 소스 (dev에선 compose bind mount가 덮어씀)
COPY . .

EXPOSE 3000

# dev: next dev, 컨테이너 밖에서 접근하도록 0.0.0.0 바인드
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0", "-p", "3000"]
