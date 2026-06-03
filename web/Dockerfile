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
# next.config.mjs reads process.env.BACKEND_ORIGIN at module load —
# which on Railway happens when `next start` runs inside the final
# container, not at build time. The build itself doesn't need this
# var (it's only used by the dev rewrites layer), so we don't set
# ENV in the build stage. Setting ENV here would FREEZE the runtime
# value to the build-time default and shadow Railway's runtime env.
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
