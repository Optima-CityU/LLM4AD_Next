FROM rust:1.99-slim-bookworm AS rust-build
ARG HGAMEY_REVISION=95f55e5c72ae28806181d299107fc57471818be5
ENV HGAMEY_BUILD_REVISION=${HGAMEY_REVISION} HGAMEY_BUILD_DIRTY=true CARGO_BUILD_JOBS=2 \
    HTTP_PROXY="" HTTPS_PROXY="" ALL_PROXY="" http_proxy="" https_proxy="" all_proxy=""
RUN mkdir -p /usr/local/cargo && printf '%s\n' \
    '[source.crates-io]' 'replace-with = "rsproxy-sparse"' \
    '[source.rsproxy-sparse]' 'registry = "sparse+https://rsproxy.cn/index/"' \
    > /usr/local/cargo/config.toml
COPY vendor/hgamey/phonefarm /src/phonefarm
WORKDIR /src/phonefarm/src
RUN --mount=type=cache,id=hgamey-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=hgamey-cargo-target,target=/src/phonefarm/src/target \
    cargo build --locked --release --bin phonefarm && \
    mkdir /out && cp target/release/phonefarm /out/phonefarm

FROM python:3.13.16-slim-bookworm AS adb-build
ARG ADB_PLATFORM_TOOLS_URL=https://dl.google.com/android/repository/platform-tools_r36.0.2-linux.zip
ARG ADB_PLATFORM_TOOLS_SHA256=3afdea91441815ab41254193df0343d92c1b1c0d0237165c3a345c8af8891c31
RUN --mount=type=cache,id=hgamey-platform-tools,target=/var/cache/platform-tools \
    python3 - "$ADB_PLATFORM_TOOLS_URL" "$ADB_PLATFORM_TOOLS_SHA256" <<'PY'
import hashlib
import pathlib
import sys
import urllib.request
import zipfile

url, expected = sys.argv[1:]
archive = pathlib.Path('/var/cache/platform-tools') / (expected + '.zip')
if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    with urllib.request.urlopen(url, timeout=120) as response:
        archive.write_bytes(response.read())
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('Android Platform Tools checksum mismatch')
with zipfile.ZipFile(archive) as bundle:
    bundle.extract('platform-tools/adb', '/out')
pathlib.Path('/out/platform-tools/adb').chmod(0o755)
PY

FROM python:3.13.16-slim-bookworm AS runtime
ENV HTTP_PROXY="" HTTPS_PROXY="" ALL_PROXY="" http_proxy="" https_proxy="" all_proxy=""
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN sed -i "s|http://deb.debian.org/debian|${APT_MIRROR%/}|g" \
      /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libusb-1.0-0 zsh ffmpeg procps util-linux glslang-tools spirv-tools && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 1000 hgamey && useradd -m -u 1000 -g 1000 hgamey
COPY --chown=1000:1000 vendor/hgamey/phonefarm /opt/phonefarm
COPY --from=adb-build /out/platform-tools/adb /usr/bin/adb
COPY --from=rust-build /out/phonefarm /opt/phonefarm/phonefarm
COPY --chown=1000:1000 dist/ /opt/executor/native/
COPY gateway/requirements.txt /tmp/gateway-requirements.txt
COPY gateway/adapter/upstream/requirements.txt /tmp/game-requirements.txt
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" \
      -r /tmp/gateway-requirements.txt -r /tmp/game-requirements.txt
ARG TRACE_PROCESSOR_URL=""
COPY gateway/fetch_trace_processor.py /tmp/fetch_trace_processor.py
RUN --mount=type=cache,id=hgamey-trace-processor,target=/var/cache/trace-processor \
    python3 /tmp/fetch_trace_processor.py --url "$TRACE_PROCESSOR_URL"
COPY --chown=1000:1000 gateway/ /opt/gateway/
COPY --chown=1000:1000 executor.py /opt/executor/executor.py
COPY --chown=1000:1000 tests/ /opt/executor/tests/
COPY --chown=1000:1000 tools/ /opt/executor/tools/
COPY --chmod=755 entrypoint.sh /opt/executor/entrypoint.sh
RUN python3 /opt/executor/tools/verify_artifacts.py /opt/executor/native && \
    mkdir -p /data /work /home/hgamey/.android && chown 1000:1000 /data /work /home/hgamey/.android && \
    mkdir -p /opt/executor/assets /opt/phonefarm/knobs/gray/build/out && \
    ln -sf /opt/executor/native/libVkLayer_refknobs.so /opt/executor/assets/libVkLayer_refknobs.so && \
    ln -sf /opt/executor/native/libVkLayer_refknobs.so /opt/phonefarm/knobs/gray/build/out/libVkLayer_refknobs.so && \
    ln -sf /opt/executor/native/refbench-mali.apk /opt/phonefarm/refbench-mali.apk && \
    ln -sf /opt/executor/native/android_aarch64_vkop_runner /opt/phonefarm/tools/vkop/android_aarch64_vkop_runner && \
    ln -sf /opt/phonefarm/phonefarm /usr/local/bin/phonefarm
ENV ADB_BIN=/usr/bin/adb ADB_VENDOR_KEYS=/run/adb-identity \
    DEVLOCK_DIR=/work/locks PHONEFARM_STATE_DIR=/work/state \
    EXECUTOR_CONFIG=/config/devices.json EXECUTOR_DATA=/data \
    EXECUTOR_BIND=127.0.0.1 EXECUTOR_PORT=8767 ANDROID_ADB_SERVER_PORT=5039 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HTTP_PROXY="" HTTPS_PROXY="" ALL_PROXY="" http_proxy="" https_proxy="" all_proxy=""
ENV PYTHONPATH=/opt/gateway/sdk:/opt/gateway/adapter:/opt/gateway:/opt/executor
USER hgamey
WORKDIR /opt/phonefarm
ENTRYPOINT ["/opt/executor/entrypoint.sh"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=15m --retries=3 CMD ["python3", "/opt/gateway/health.py"]
CMD ["gateway"]
