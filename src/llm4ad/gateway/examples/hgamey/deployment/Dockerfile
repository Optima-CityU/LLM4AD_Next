ARG RUST_IMAGE=rust:1.99-slim-bookworm
ARG PYTHON_IMAGE=python:3.13.16-slim-bookworm
ARG HGAMEY_NATIVE_IMAGE=native-runtime

FROM ${RUST_IMAGE} AS rust-build
ARG HGAMEY_REVISION=95f55e5c72ae28806181d299107fc57471818be5
ARG CARGO_REGISTRY_MIRROR=""
ENV HGAMEY_BUILD_REVISION=${HGAMEY_REVISION} HGAMEY_BUILD_DIRTY=true CARGO_BUILD_JOBS=2
RUN if [ -n "$CARGO_REGISTRY_MIRROR" ]; then \
      mkdir -p /usr/local/cargo && printf '%s\n' \
      '[source.crates-io]' 'replace-with = "gateway-mirror"' \
      '[source.gateway-mirror]' "registry = \"$CARGO_REGISTRY_MIRROR\"" \
      > /usr/local/cargo/config.toml; \
    fi
COPY native/vendor/hgamey/phonefarm /src/phonefarm
WORKDIR /src/phonefarm/src
RUN --mount=type=cache,id=hgamey-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=hgamey-cargo-target,target=/src/phonefarm/src/target \
    cargo build --locked --release --bin phonefarm && \
    mkdir /out && cp target/release/phonefarm /out/phonefarm

FROM ${PYTHON_IMAGE} AS android-build
ARG APT_MIRROR=""
ARG ANDROID_REPOSITORY_BASE=https://dl.google.com/android/repository
ENV ANDROID_REPOSITORY_BASE=${ANDROID_REPOSITORY_BASE} ANDROID_DOWNLOAD_CACHE=/var/cache/hgamey/android
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi && apt-get update && apt-get install -y --no-install-recommends \
      g++ openjdk-17-jdk-headless ca-certificates unzip file && \
    rm -rf /var/lib/apt/lists/*
COPY native/ /build/
RUN --mount=type=cache,id=hgamey-android-downloads,target=/var/cache/hgamey/android \
    --mount=type=cache,id=hgamey-refbench-signing,target=/var/cache/hgamey/signing \
    python3 /build/build.py --toolchain /opt/android --output /artifacts \
      --signing-key /var/cache/hgamey/signing/refbench-debug.keystore && \
    python3 /build/fetch_adb.py --output /artifacts/adb --cache /var/cache/hgamey/android

FROM ${PYTHON_IMAGE} AS native-runtime
ARG APT_MIRROR=""
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi && apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libusb-1.0-0 zsh ffmpeg procps util-linux glslang-tools spirv-tools && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 1000 hgamey && useradd -m -u 1000 -g 1000 hgamey
COPY protocol/upstream/requirements.txt /tmp/game-requirements.txt
RUN python3 -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r /tmp/game-requirements.txt
ARG TRACE_PROCESSOR_URL=""
COPY native/fetch_trace_processor.py /tmp/fetch_trace_processor.py
RUN --mount=type=cache,id=hgamey-trace-processor,target=/var/cache/trace-processor \
    python3 /tmp/fetch_trace_processor.py --url "$TRACE_PROCESSOR_URL"
COPY --chown=1000:1000 native/vendor/hgamey/phonefarm /opt/phonefarm
COPY --chown=1000:1000 native/vendor/hgamey/refbench /opt/refbench
COPY --from=rust-build /out/phonefarm /opt/phonefarm/phonefarm
COPY --from=android-build /artifacts/ /opt/hgamey/artifacts/
COPY --from=android-build /artifacts/adb /usr/bin/adb
COPY --from=android-build /opt/android/ndk /opt/android/ndk
RUN mkdir -p /data /work /home/hgamey/.android /opt/phonefarm/knobs/gray/build/out && \
    chown 1000:1000 /data /work /home/hgamey/.android && \
    ln -s /opt/phonefarm/phonefarm /usr/local/bin/phonefarm && \
    ln -s /opt/hgamey/artifacts/libVkLayer_refknobs.so /opt/phonefarm/knobs/gray/build/out/libVkLayer_refknobs.so && \
    ln -s /opt/hgamey/artifacts/refbench-mali.apk /opt/phonefarm/refbench-mali.apk && \
    ln -s /opt/hgamey/artifacts/android_aarch64_vkop_runner /opt/phonefarm/tools/vkop/android_aarch64_vkop_runner
ENV ADB_BIN=/usr/bin/adb ADB_VENDOR_KEYS=/run/adb-identity \
    ANDROID_ADB_SERVER_PORT=5039 REFBENCH_NDK=/opt/android/ndk \
    HGAMEY_LAYER_PATH=/opt/hgamey/artifacts/libVkLayer_refknobs.so \
    DEVLOCK_DIR=/work/locks PHONEFARM_STATE_DIR=/work/state \
    PYTHONUNBUFFERED=1 PYTHONPATH=""

FROM ${HGAMEY_NATIVE_IMAGE} AS gateway
USER root
ARG LLM4AD_PACKAGE=llm4ad-next
ARG LLM4AD_EXTRAS=""
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY deployment/wheels/ /wheels/
RUN extras=""; \
    if [ -n "$LLM4AD_EXTRAS" ]; then extras="[$LLM4AD_EXTRAS]"; fi; \
    if ls /wheels/llm4ad_next-*.whl >/dev/null 2>&1; then \
      for wheel in /wheels/llm4ad_next-*.whl; do \
        python3 -m pip install --no-cache-dir "${wheel}${extras}"; \
        python3 -m pip install --no-cache-dir --no-deps --force-reinstall "$wheel"; \
      done; \
    else \
      package="$LLM4AD_PACKAGE"; \
      if [ -n "$extras" ]; then \
        package="llm4ad-next${extras}${LLM4AD_PACKAGE#llm4ad-next}"; \
      fi; \
      python3 -m pip install --no-cache-dir "$package"; \
    fi
ENV PYTHONUNBUFFERED=1 PYTHONPATH="" LLM4AD_GATEWAY_ADAPTER=hgamey
USER hgamey
WORKDIR /data
EXPOSE 8768
ENTRYPOINT ["python3", "-m", "llm4ad.gateway"]
CMD ["start", "--no-ui"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=15m --retries=3 \
  CMD ["python3", "-m", "llm4ad.gateway", "health", "--state-dir", "/data/gateway"]
