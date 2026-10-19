# hawserd: API server and durable worker in one image (ADR-0010).
# Build from the repository root:  docker build -f deploy/docker/Dockerfile -t hawser/hawserd .

FROM node:20-alpine AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@10 --activate
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY api/openapi/openapi.json /api/openapi/openapi.json
COPY web ./
RUN pnpm build

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.21 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_HTTP_TIMEOUT=300
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY python ./python
COPY --from=web /web/dist ./python/hawser_server/src/hawser_server/static
RUN uv sync --frozen --no-dev --all-packages --all-extras

FROM python:3.12-slim-bookworm AS runtime
# OpenCV (used by the bundled OCR engine) needs these shared libraries at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system hawser && useradd --system --gid hawser --home /app --shell /usr/sbin/nologin hawser
WORKDIR /app
COPY --from=build --chown=hawser:hawser /app/.venv /app/.venv
COPY --from=build --chown=hawser:hawser /app/python /app/python
COPY --chown=hawser:hawser skills ./skills
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HAWSER_HOME=/data/hawser \
    HAWSER_SKILL_PATHS=/app/skills \
    HAWSERD_HOST=0.0.0.0 \
    HAWSERD_PORT=8080
RUN mkdir -p /data/hawser && chown -R hawser:hawser /data
USER hawser
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"
ENTRYPOINT ["hawserd"]
CMD ["serve"]
