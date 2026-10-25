# syntax=docker/dockerfile:1
ARG NODE_IMAGE=node:24.22.1-bookworm-slim
ARG NGINX_IMAGE=nginx:1.30.6-alpine
FROM ${NODE_IMAGE} AS builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --include=dev
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM ${NGINX_IMAGE} AS runtime
COPY --from=builder /build/dist /usr/share/nginx/html
COPY infrastructure/nginx/nginx.conf.template /opt/finscope/nginx.conf.template
COPY infrastructure/nginx/start.sh /opt/finscope/start.sh
RUN addgroup -g 10001 finscope && adduser -D -u 10001 -G finscope finscope && chmod 755 /opt/finscope/start.sh
USER 10001:10001
EXPOSE 8080 8443
ENTRYPOINT ["/bin/sh", "/opt/finscope/start.sh"]
