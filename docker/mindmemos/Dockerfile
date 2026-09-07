FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app/mindmemos
ENV PATH="/app/mindmemos/.venv/bin:$PATH"

ARG UV_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple

COPY third_party/MindMemOS/pyproject.toml third_party/MindMemOS/uv.lock ./
COPY third_party/MindMemOS/README.md ./README.md
COPY third_party/MindMemOS/src ./src
COPY third_party/MindMemOS/config ./config
COPY third_party/MindMemOS/scripts ./scripts
COPY docker/mindmemos/render_config.py ./render_config.py

RUN --mount=type=cache,id=uv-mindmemos,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package mindmemos

CMD ["sh", "-c", "python /app/mindmemos/render_config.py && MINDMEMOS_CONFIG_PATH=${MINDMEMOS_RENDERED_CONFIG:-/tmp/mindmemos-dev.yaml} python -m uvicorn mindmemos.api.app:app --host 0.0.0.0 --port 8000"]
