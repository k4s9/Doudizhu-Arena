# Stage 1: Build the Vue/Vite frontend
FROM node:22-alpine AS build

WORKDIR /app

# Install dependencies (cached unless package.json or lockfile changes)
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine

# Replace default nginx config with our custom one
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from stage 1
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
