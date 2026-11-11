# Ember & Tide: one image, one port. Web app is built and served by the core.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
LABEL org.opencontainers.image.title="Ember & Tide" \n      org.opencontainers.image.description="Local-first controller for a dual-zone mattress topper" \n      org.opencontainers.image.source="https://github.com/Ncbudd/ember-tide" \n      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
WORKDIR /app
COPY core/ /app/core
RUN pip install --no-cache-dir /app/core
COPY --from=web /web/dist /app/web
ENV ET_WEB_DIR=/app/web \
    ET_DATA_DIR=/data \
    ET_PORT=8790
VOLUME /data
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8790/healthz')" || exit 1
CMD ["python", "-m", "embertide"]
