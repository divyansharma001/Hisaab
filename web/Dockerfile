FROM node:22-alpine AS deps
WORKDIR /app
# The lockfile is copied too, so npm ci installs exact pinned versions.
COPY package.json package-lock.json ./
RUN npm ci


# Dev runs the Vite server. Source arrives by bind mount.
FROM deps AS dev
EXPOSE 5173
CMD ["npm", "run", "dev"]


# Build once, then serve the static files. Used for the demo image.
FROM deps AS builder
COPY . .
RUN npm run build

FROM nginx:alpine AS runtime
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
