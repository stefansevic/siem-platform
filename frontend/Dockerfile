# ============================================
# Frontend - SIEM Dashboard
# ============================================
# Multi-stage build:
#   Stage 1: Vite compiles TypeScript + React into static assets.
#   Stage 2: A small nginx image serves the assets.
#
# The dev workflow uses `npm run dev` directly on the host (faster
# iteration). This Dockerfile is for production-like demos and the
# eventual cross-platform check.

# -------- Stage 1: build --------
FROM node:22-alpine AS build

WORKDIR /app

# Cache dependencies separately from source for faster rebuilds.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# -------- Stage 2: serve --------
FROM nginx:1.27-alpine

# Single-page-app routing: every unknown path falls back to index.html
# so React Router handles the URL on the client side.
RUN rm /etc/nginx/conf.d/default.conf
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]