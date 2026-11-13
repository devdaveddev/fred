# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
# Bake the open pre-trained embedding model into the image: no runtime downloads in production.
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"
COPY . .

FROM python:3.13-slim AS runtime
RUN useradd --create-home --uid 10001 fred
WORKDIR /app
COPY --from=build --chown=fred:fred /app /app
COPY --from=build --chown=fred:fred /models /models
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 MODEL_CACHE=/models HF_HUB_OFFLINE=1
USER fred
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["python", "-m", "app.healthcheck"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/api", "--proxy-headers", "--forwarded-allow-ips=*"]
