ARG PYTHON_IMAGE=python:3.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.26
FROM ${UV_IMAGE} AS uv-tools

FROM ${PYTHON_IMAGE} AS runtime

ARG APT_MIRROR=""
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources && \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi && apt-get update && apt-get install -y --no-install-recommends \
      adb bubblewrap ca-certificates libgcc-s1 libstdc++6 && rm -rf /var/lib/apt/lists/*

FROM runtime
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG LLM4AD_PACKAGE=llm4ad-next[gateway-ui]
COPY deployment/ /tmp/filament-deployment/
COPY requirements.txt /tmp/filament-requirements.txt
RUN --mount=from=uv-tools,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv set -eu; \
    set -- /tmp/filament-deployment/wheels/llm4ad_next-*.whl; \
    if [ -f "$1" ]; then \
      test "$#" -eq 1; \
      LLM4AD_PACKAGE="$1[gateway-ui]"; \
    fi; \
    uv pip install --system --link-mode copy --index-url "$PIP_INDEX_URL" \
      "$LLM4AD_PACKAGE" -r /tmp/filament-requirements.txt; \
    rm -rf /tmp/filament-deployment /tmp/filament-requirements.txt

ENV PYTHONUNBUFFERED=1 HOME=/state LLM4AD_GATEWAY_STATE_DIR=/state
WORKDIR /state
USER 1000:1000
ENTRYPOINT ["llm4ad", "evaluator-gateway"]
CMD ["start", "--adapter", "filament", "--no-open-browser"]
HEALTHCHECK --interval=20s --timeout=5s --start-period=20m --retries=3 \
  CMD ["llm4ad", "evaluator-gateway", "health", "--state-dir", "/state"]
