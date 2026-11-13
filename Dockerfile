# Ember & Tide: one image, one port. Web app is built and served by the core.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
LABEL org.opencontainers.image.title="Ember & Tide" \
      org.opencontainers.image.description="Local-first controller for a dual-zone mattress topper" \
      org.opencontainers.image.source="https://github.com/Ncbudd/ember-tide" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
WORKDIR /app
COPY core/ /app/core
RUN pip install --no-cache-dir /app/core
COPY --from=web /web/dist /app/web
ENV ET_WEB_DIR=/app/web \
    ET_DATA_DIR=/data \
    ET_PORT=8790
# Run as an unprivileged user (uid/gid 1000). /data is created owned by it, so a fresh named volume just works;
# an existing bind-mounted data folder written by the old root image needs a one-time `chown -R 1000:1000` (docs/install.md).
RUN groupadd --system --gid 1000 embertide && useradd --system --uid 1000 --gid 1000 --no-create-home embertide     && mkdir -p /data && chown embertide:embertide /data
USER 1000:1000
VOLUME /data
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8790/healthz')" || exit 1
CMD ["python", "-m", "embertide"]
