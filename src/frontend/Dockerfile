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
ARG VITE_FOOTER_BEIAN

RUN APP_VERSION="$(tr -d '\r\n' < /app/VERSION)" && \
    if [ "$APP_VERSION" = "develop" ]; then \
      VITE_APP_VERSION="develop"; \
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
