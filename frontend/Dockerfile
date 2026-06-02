# syntax=docker/dockerfile:1


# Multi-stage build (different stages, different filesystem)
# Stage 1: Install pakages
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./

# Docker cache for npm downloads
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Stage 2: Build Next.js app
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY

RUN --mount=type=cache,target=/root/.npm \
    npm run build

# Stage 3: final small runtime image
FROM node:20-alpine AS runner
WORKDIR /frontend

RUN addgroup --system --gid 1001 group_2
RUN adduser --system --uid 1001 user_2

COPY --from=builder /app/public ./public
COPY --from=builder --chown=user_2:group_2 /app/.next/standalone ./
COPY --from=builder --chown=user_2:group_2 /app/.next/static ./.next/static

ENV PORT=2011 \
    HOSTNAME=0.0.0.0

USER user_2

CMD ["node", "server.js"]