# Cloudsail Support bot.
# Multi-arch: build natively on the ARM64 host (`docker compose build`), or
# cross-build with `docker buildx build --platform linux/arm64 .`
FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev \
    && chmod +x entrypoint.sh \
    && useradd --system --uid 10001 --no-create-home bot

USER bot
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '3000'), timeout=4)"

ENTRYPOINT ["./entrypoint.sh"]
