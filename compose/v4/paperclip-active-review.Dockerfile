# Build with the compose/v4 directory as context, after building the pinned private image.
FROM ralph-paperclip-private:2026.1117.0 AS ui
COPY paperclip-active-review.patch /tmp/active-review.patch
WORKDIR /app
RUN git apply /tmp/active-review.patch
WORKDIR /app/ui
RUN RAYON_NUM_THREADS=1 UV_THREADPOOL_SIZE=1 GOMAXPROCS=1 NODE_OPTIONS=--max-old-space-size=1280 ./node_modules/.bin/vite build --minify=false --outDir /tmp/review-ui-dist
FROM ralph-paperclip-private:2026.1117.0
COPY --from=ui /tmp/review-ui-dist/ /app/ui/dist/
