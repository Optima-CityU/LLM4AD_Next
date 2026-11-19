# ============================================================
# LLM4AD Web 后端服务镜像
# ============================================================

# ---------- 基础镜像 ----------
ARG DOCKERHUB_REGISTRY=""
FROM ${DOCKERHUB_REGISTRY}library/python:3.12-bookworm

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
ARG GITHUB_PROXY_URL=""
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s@deb.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources && \
      sed -i "s@security.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources; \
    fi
# Install the pinned offline Markdown converter and network sandbox library.
ARG PANDOC_VERSION="3.10.2"
ARG PANDOC_SHA256=""
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libseccomp2 fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(dpkg --print-architecture)" in \
      amd64) pandoc_arch=amd64; pandoc_sha=c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a ;; \
      arm64) pandoc_arch=arm64; pandoc_sha=1c4d69f2a092bd47cb180e58a4aab7b9637101ced928252458c7d41a7f7fa71d ;; \
      *) echo "Unsupported Pandoc architecture" >&2; exit 1 ;; \
    esac \
    && if [ "$PANDOC_VERSION" != "3.10.2" ] && [ -z "$PANDOC_SHA256" ]; then \
      echo "Set PANDOC_SHA256 when overriding the pinned Pandoc version" >&2; exit 1; \
    fi \
    && pandoc_url="https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-${pandoc_arch}.tar.gz" \
    && if [ -n "$GITHUB_PROXY_URL" ]; then pandoc_url="${GITHUB_PROXY_URL%/}/${pandoc_url}"; fi \
    && curl -fsSL --retry 3 --max-time 180 "$pandoc_url" -o /tmp/pandoc.tar.gz \
    && echo "${PANDOC_SHA256:-$pandoc_sha}  /tmp/pandoc.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/pandoc.tar.gz -C /usr/local/bin --strip-components=2 "pandoc-${PANDOC_VERSION}/bin/pandoc" \
    && rm /tmp/pandoc.tar.gz \
    && pandoc --version

# Reuse the exact pinned browser fonts, including CJK and mathematical faces.
COPY src/frontend/scripts/fetch-typst-fonts.sh /opt/llm4ad-render/scripts/fetch-typst-fonts.sh
RUN --mount=type=cache,id=llm4ad-typst-fonts,sharing=locked,target=/var/cache/llm4ad/typst-fonts \
    TYPST_FONT_CACHE_DIR=/var/cache/llm4ad/typst-fonts \
    bash /opt/llm4ad-render/scripts/fetch-typst-fonts.sh \
    && mkdir -p /usr/local/share/fonts/llm4ad \
    && cp -a /opt/llm4ad-render/public/typst-fonts/. /usr/local/share/fonts/llm4ad/ \
    && ln -s /usr/local/share/fonts/llm4ad /app/typst-fonts \
    && fc-cache -f /usr/local/share/fonts/llm4ad \
    && rm -rf /opt/llm4ad-render
ENV TYPST_FONT_DIR=/app/typst-fonts

COPY src/backend/uv.lock uv.lock
RUN --mount=type=cache,id=uv-backend,target=/root/.cache/uv \
    --mount=type=bind,source=src/backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=pyproject.toml,target=/pyproject.toml \
    --mount=type=bind,source=README.md,target=/README.md \
    if [ -n "$GITHUB_PROXY_URL" ]; then \
      export GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.${GITHUB_PROXY_URL%/}/https://github.com/.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/"; \
    fi && \
    uv sync --no-install-workspace --extra arc-templates

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
# arc-templates 提供 ARC-Bench 课题注册表（researchclaw.arc_bench），供 API 侧列出
# 模板并在建会话时落 stage-07/08/09 产物。刻意不含 torch —— 那是任务容器的 extra。
RUN --mount=type=cache,id=uv-backend,target=/root/.cache/uv \
    --mount=type=bind,source=src/backend/pyproject.toml,target=pyproject.toml \
    if [ -n "$GITHUB_PROXY_URL" ]; then \
      export GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.${GITHUB_PROXY_URL%/}/https://github.com/.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/"; \
    fi && \
    uv sync --extra arc-templates

# ---------- 启动服务 ----------
CMD ["fastapi", "run", "--workers", "4", "app/main.py"]
