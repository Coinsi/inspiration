# 构建阶段
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
RUN rm -rf dist/media

# 运行阶段(nginx 托管静态资源 + 代理 /api)
FROM nginx:alpine
COPY public/media /usr/share/nginx/html/media
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
