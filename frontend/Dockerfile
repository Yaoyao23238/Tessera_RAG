# syntax=docker/dockerfile:1
# Tessera 前端镜像：vite 构建静态产物 + nginx 托管（/api、/ws 反代后端）
FROM node:22-alpine AS build
WORKDIR /app

# 无 lockfile，直接 npm install（含 devDependencies：tsc/vite 构建需要）
COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
