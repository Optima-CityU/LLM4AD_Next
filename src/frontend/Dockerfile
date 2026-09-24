# syntax=docker/dockerfile:1.7

# Stage 0, "build-stage", based on Bun, to build and compile the frontend
FROM oven/bun:1 AS build-stage

WORKDIR /app

COPY src/frontend/package.json src/frontend/bun.lock /app/

RUN bun install

# Keep the large, pinned Typst fonts independent from normal source changes.
# The cache mount also survives an invalidated layer and serializes concurrent
# builds that share the same BuildKit cache directory.
COPY src/frontend/scripts/fetch-typst-fonts.sh /app/scripts/
RUN --mount=type=cache,id=llm4ad-typst-fonts,sharing=locked,target=/var/cache/llm4ad/typst-fonts \
    TYPST_FONT_CACHE_DIR=/var/cache/llm4ad/typst-fonts \
    bash /app/scripts/fetch-typst-fonts.sh

COPY ./src/frontend /app
COPY ./docs /docs
COPY ./VERSION /app/VERSION
ARG VITE_API_URL
ARG VITE_CODE_SERVER_URL
ARG VITE_FOOTER_BEIAN

RUN APP_VERSION="$(tr -d '\r\n' < /app/VERSION)" && \
    if [ "$APP_VERSION" = "develop" ]; then \
      VITE_APP_VERSION="develop"; \
    elif [ "${APP_VERSION#v}" != "$APP_VERSION" ]; then \
      VITE_APP_VERSION="$APP_VERSION"; \
    else \
      VITE_APP_VERSION="v$APP_VERSION"; \
    fi && \
    VITE_APP_VERSION="$VITE_APP_VERSION" \
    VITE_FOOTER_BEIAN="${VITE_FOOTER_BEIAN:-}" \
    bun run build


# Stage 1, based on Nginx, to have only the compiled app, ready for production with Nginx
FROM nginx:alpine

COPY --from=build-stage /app/dist/ /usr/share/nginx/html

COPY ./src/frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Compose mounts deployment-specific proxy configuration into this directory.
RUN mkdir -p /etc/nginx/extra-conf.d

# The frontend configuration is complete at build time and does not use the
# stock image's envsubst templates. Start Nginx directly to avoid its package
# checksum and entrypoint hook scans on every container start.
ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
