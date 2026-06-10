# syntax=docker/dockerfile:1


# Multi-stage build (different stages, different filesystem)
# Stage 1: Install pakages
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./

# Docker cache for npm downloads
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Stage 2 (dev): hot-reload server (webpack + polling)
FROM node:20-alpine AS dev
WORKDIR /app
ENV PORT=2011 \
    HOSTNAME=0.0.0.0 \
    WATCHPACK_POLLING=true \
    CHOKIDAR_USEPOLLING=true \
    CHOKIDAR_INTERVAL=1000
COPY --from=deps /app/node_modules ./node_modules

CMD ["npm", "run", "dev:docker"]