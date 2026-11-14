# syntax=docker/dockerfile:1.7
# Single-service image: the website and the API in one container (e.g. one Render "Web Service").
# The public port ($PORT) is served by Next.js, which proxies /api/* to the API on 127.0.0.1:8000.
# For the multi-service setup use render.yaml (Blueprint) or docker-compose.yml instead.

# ---------- website
FROM node:22-bookworm-slim AS web
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

# ---------- API (dependencies + the pre-trained embedding model, baked in)
FROM python:3.13-slim AS api
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"
COPY backend/ ./

# ---------- runtime
FROM python:3.13-slim
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
RUN useradd --create-home --uid 10001 fred
COPY --from=api --chown=fred:fred /app /app
COPY --from=api --chown=fred:fred /models /models
COPY --from=web --chown=fred:fred /web/.next/standalone /web
COPY --from=web --chown=fred:fred /web/.next/static /web/.next/static
COPY --chown=fred:fred deploy/start.sh /start.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 MODEL_CACHE=/models HF_HUB_OFFLINE=1 \
    NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=10000 \
    API_INTERNAL_URL=http://127.0.0.1:8000 EMBEDDED_WORKER=true \
    MALLOC_ARENA_MAX=2 ANALYSIS_CACHE=2 WARM_ON_START=false
USER fred
EXPOSE 10000
CMD ["sh", "/start.sh"]
