# ResolveRight: API + Gradio UI. One worker only (local Qdrant allows one process per folder).
FROM python:3.12-slim

# uv from the official image (course Session 9 pattern: uv sync --frozen)
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so this layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-group eval --no-install-project

COPY app ./app
COPY data ./data
COPY prompts ./prompts

# Run as a non-root user; the app writes only to logs/, qdrant_db/ and .cache/.
# chown just those folders: a recursive chown of /app would copy the whole .venv into a new
# image layer (about 2.4 GB extra).
RUN useradd --create-home appuser \
    && mkdir -p logs qdrant_db .cache \
    && chown appuser:appuser logs qdrant_db .cache
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import os,urllib.request as u; u.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/health', timeout=4)"

# Secrets (OPENAI_API_KEY, LANGFUSE_*) come from the host's environment, never from the image.
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
