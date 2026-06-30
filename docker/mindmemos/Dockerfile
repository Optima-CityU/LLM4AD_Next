FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app/mindmemos
ENV PATH="/app/mindmemos/.venv/bin:$PATH"

ARG UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY third_party/MindMemOS/pyproject.toml third_party/MindMemOS/uv.lock ./
COPY third_party/MindMemOS/README.md ./README.md
COPY third_party/MindMemOS/src ./src
COPY third_party/MindMemOS/config ./config
COPY third_party/MindMemOS/scripts ./scripts

RUN --mount=type=cache,id=uv-mindmemos,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package mindmemos

CMD ["python", "-m", "uvicorn", "mindmemos.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
