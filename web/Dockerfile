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
# BACKEND_ORIGIN is read at build time by next.config.mjs to bake the
# rewrites in. Provide a placeholder default so the build doesn't
# fail when the env isn't set yet; the real value is injected on
# Railway via the service's env var.
ARG BACKEND_ORIGIN=http://localhost:8001
ENV BACKEND_ORIGIN=$BACKEND_ORIGIN
RUN npm run build

FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/next.config.mjs ./next.config.mjs
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["sh", "-c", "npx next start -p ${PORT:-3000}"]
