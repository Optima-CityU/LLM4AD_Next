# Stage 0, "build-stage", based on Bun, to build and compile the frontend
FROM oven/bun:1 AS build-stage

WORKDIR /app

COPY src/frontend/package.json src/frontend/bun.lock /app/

RUN bun install

COPY ./src/frontend /app
COPY ./docs /docs
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
ARG VITE_ADMIN_ANALYTICS_URL

RUN bun run build


# Stage 1, based on Nginx, to have only the compiled app, ready for production with Nginx
FROM nginx:1

COPY --from=build-stage /app/dist/ /usr/share/nginx/html

COPY ./src/frontend/nginx.conf /etc/nginx/conf.d/default.conf

# 创建空的 extra-conf.d 目录，部署时通过 compose volume 挂载具体配置
RUN mkdir -p /etc/nginx/extra-conf.d
