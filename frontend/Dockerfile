# syntax=docker/dockerfile:1.7

FROM node:22.12-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . .
RUN npm run build


FROM nginx:1.28-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-proxy-headers.conf /etc/nginx/proxy-headers.conf
COPY nginx-api-proxy.conf /etc/nginx/api-proxy.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --output-document=/dev/null http://127.0.0.1/internal-health || exit 1

CMD ["nginx", "-g", "daemon off;"]
