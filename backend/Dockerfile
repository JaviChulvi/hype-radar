# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && \
    useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
