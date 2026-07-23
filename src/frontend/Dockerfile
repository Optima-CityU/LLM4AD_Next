# Stage 0, "build-stage", based on Bun, to build and compile the frontend
FROM oven/bun:1 AS build-stage

WORKDIR /app

COPY src/frontend/package.json src/frontend/bun.lock /app/

RUN bun install

COPY ./src/frontend /app
COPY ./docs /docs
COPY ./VERSION /app/VERSION
ARG VITE_API_URL
ARG VITE_CODE_SERVER_URL
ARG VITE_PLAUSIBLE_ENABLED
ARG VITE_PLAUSIBLE_DOMAIN
ARG VITE_PLAUSIBLE_ENDPOINT
ARG VITE_PLAUSIBLE_AUTO_CAPTURE_PAGEVIEWS
ARG VITE_PLAUSIBLE_HASH_BASED_ROUTING
ARG VITE_PLAUSIBLE_OUTBOUND_LINKS
ARG VITE_PLAUSIBLE_FILE_DOWNLOADS
ARG VITE_PLAUSIBLE_FORM_SUBMISSIONS
ARG VITE_PLAUSIBLE_CAPTURE_ON_LOCALHOST
ARG VITE_PLAUSIBLE_LOGGING
ARG VITE_PLAUSIBLE_BIND_TO_WINDOW
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

# 创建空的 extra-conf.d 目录，部署时通过 compose volume 挂载具体配置
RUN mkdir -p /etc/nginx/extra-conf.d
