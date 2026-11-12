FROM python:3.12-slim
ARG LLM4AD_PACKAGE=llm4ad-next=={{SDK_VERSION}}
ARG LLM4AD_EXTRAS=""
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY wheels/ /wheels/
RUN extras=""; \
    if [ -n "$LLM4AD_EXTRAS" ]; then extras="[$LLM4AD_EXTRAS]"; fi; \
    if ls /wheels/llm4ad_next-*.whl >/dev/null 2>&1; then \
      for wheel in /wheels/llm4ad_next-*.whl; do \
        python -m pip install --no-cache-dir "${wheel}${extras}"; \
        python -m pip install --no-cache-dir --no-deps --force-reinstall "$wheel"; \
      done; \
    else \
      package="$LLM4AD_PACKAGE"; \
      if [ -n "$extras" ]; then \
        package="llm4ad-next${extras}${LLM4AD_PACKAGE#llm4ad-next}"; \
      fi; \
      python -m pip install --no-cache-dir "$package"; \
    fi
COPY requirements.txt /tmp/evaluator-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/evaluator-requirements.txt
ENV PYTHONUNBUFFERED=1
WORKDIR /evaluator
EXPOSE 8768
CMD ["python", "-m", "llm4ad.gateway", "start", "--no-ui"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-m", "llm4ad.gateway", "health", "--state-dir", "/state"]
