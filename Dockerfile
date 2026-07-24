FROM node:22-alpine AS frontend-build
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.10.4 AS uv

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY backend/ ./
RUN uv sync --no-dev
COPY --from=frontend-build /web/dist ./app/static

RUN chown -R app:app /app
USER app

EXPOSE 8000
CMD ["sh", "./start.sh"]
