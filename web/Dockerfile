# Frontend (Next.js 14) — deployed as the `james-os-frontend` service.
# Multi-stage build keeps the runtime image lean.

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --include=dev

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Next bakes rewrites into .next/routes-manifest.json at BUILD time,
# so BACKEND_ORIGIN must be present when `npm run build` runs. Railway
# auto-injects service env vars as docker build args when the Dockerfile
# declares them as ARG — the ENV line then exposes the value to the
# `npm run build` process. We only set this in the build stage; the
# run stage stays clean so Railway's runtime env still wins there
# (next.config.mjs also re-reads inside rewrites() as a safety net).
ARG BACKEND_ORIGIN
ENV BACKEND_ORIGIN=$BACKEND_ORIGIN
RUN npm run build

FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/next.config.mjs ./next.config.mjs
# /app/public is optional — Next.js auto-creates routes for any static
# assets there, but the project doesn't have any. Skipping the COPY.
EXPOSE 3000
CMD ["sh", "-c", "npx next start -p ${PORT:-3000}"]
