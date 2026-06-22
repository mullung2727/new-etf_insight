# broker — Kiwoom FastAPI + MCP gateway (dev image)
FROM python:3.12-slim

# uv (deps manager) — copy static binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 의존성 레이어 (소스보다 먼저 → 캐시 활용)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 소스 (dev에선 compose bind mount가 덮어씀; 이미지 COPY는 prod/캐시 대비)
COPY . .

# uv가 만든 venv를 PATH에 — uvicorn 직접 호출 가능
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8001

# dev: 핫리로드
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
