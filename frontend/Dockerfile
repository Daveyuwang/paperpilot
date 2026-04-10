FROM node:20-alpine AS base
WORKDIR /app

# ── Development ────────────────────────────────────────────────────────────
FROM base AS development
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

# ── Build ──────────────────────────────────────────────────────────────────
FROM base AS builder
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# ── Production (serve static) ──────────────────────────────────────────────
FROM nginx:alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
COPY infra/nginx-frontend.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
