# ============================================================
# LLM4AD Web 后端服务镜像
# ============================================================

# ---------- 基础镜像 ----------
FROM python:3.12-bookworm

# 禁用 Python 输出缓冲，保证日志实时输出
ENV PYTHONUNBUFFERED=1

# ---------- 安装 uv 包管理器 ----------
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# 预编译字节码，加快启动速度
ENV UV_COMPILE_BYTECODE=1
# 使用 copy 模式安装依赖，避免硬链接问题
ENV UV_LINK_MODE=copy

# ---------- 工作目录 ----------
WORKDIR /app/backend/

# 将虚拟环境加入 PATH
ENV PATH="/app/backend/.venv/bin:$PATH"

# ---------- 安装依赖（不含工作区包，利用 Docker 层缓存）----------
ARG UV_INDEX_URL
ARG GITHUB_PROXY_URL=https://gh-proxy.org
COPY src/backend/uv.lock uv.lock
RUN --mount=type=cache,id=uv-backend,target=/root/.cache/uv \
    --mount=type=bind,source=src/backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=pyproject.toml,target=/pyproject.toml \
    --mount=type=bind,source=README.md,target=/README.md \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.${GITHUB_PROXY_URL}/https://github.com/.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/" \
    uv sync --no-install-workspace

# ---------- 复制应用代码 ----------
COPY ./src/backend/scripts    /app/backend/scripts
COPY ./src/backend/pyproject.toml ./src/backend/alembic.ini /app/backend/
COPY ./src/backend/cli.py     /app/backend/
COPY ./src/backend/app        /app/backend/app
COPY ./examples   /app/backend/examples

# 复制 llm4ad 核心库及根项目配置
COPY ./src/llm4ad             /src/llm4ad
COPY ./pyproject.toml     /pyproject.toml
COPY ./README.md          /README.md

# ---------- 安装工作区包 ----------
RUN --mount=type=cache,id=uv-backend,target=/root/.cache/uv \
    --mount=type=bind,source=src/backend/pyproject.toml,target=pyproject.toml \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.${GITHUB_PROXY_URL}/https://github.com/.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/" \
    uv sync

# ---------- 启动服务 ----------
CMD ["fastapi", "run", "--workers", "4", "app/main.py"]
