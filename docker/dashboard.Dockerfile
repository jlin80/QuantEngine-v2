# ============================================================================
# Quant Engine V2 — dashboard (Next.js + React + TypeScript + Tailwind).
# PREPARADO para la fase de dashboard: la interfaz aún no se desarrolla.
# Cuando exista `dashboard/package.json`, este Dockerfile funcionará sin cambios.
# ============================================================================
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
