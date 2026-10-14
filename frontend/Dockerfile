FROM public.ecr.aws/docker/library/node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
ARG NEUROWEAVE_REVISION=development
RUN npm run build && node -e "require('fs').writeFileSync('dist/__frontend-version.json', JSON.stringify({revision:process.env.NEUROWEAVE_REVISION}))"

FROM public.ecr.aws/docker/library/nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236 AS runtime
ARG NEUROWEAVE_REVISION=development
LABEL org.opencontainers.image.revision=${NEUROWEAVE_REVISION}
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3000
