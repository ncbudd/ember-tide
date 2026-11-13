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
# Debian security fixes published after the base image was built (the release scan fails on fixed HIGH/CRITICAL ones)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
COPY core/ /app/core
RUN pip install --no-cache-dir "/app/core[claude]"
COPY --from=web /web/dist /app/web
ENV ET_WEB_DIR=/app/web \
    ET_DATA_DIR=/data \
    ET_PORT=8790
# The app runs as an unprivileged user (uid/gid 1000). The entrypoint starts as root only to set up a NEW data folder
# (a bind-mounted ./data that Docker created owned by root) and then drops to uid 1000; existing data owned by someone
# else keeps the old root behaviour. A Compose `user:` line skips all of this. See docker/entrypoint.sh.
RUN groupadd --system --gid 1000 embertide && useradd --system --uid 1000 --gid 1000 --no-create-home embertide     && mkdir -p /data && chown embertide:embertide /data
COPY --chmod=755 docker/entrypoint.sh /app/entrypoint.sh
# nosemgrep: dockerfile.security.missing-user.missing-user, dockerfile.security.missing-user-entrypoint.missing-user-entrypoint -- the entrypoint drops to uid 1000 itself (setpriv) once the data folder is set up
ENTRYPOINT ["/app/entrypoint.sh"]
VOLUME /data
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:${ET_PORT:-8790}/healthz')" || exit 1
CMD ["python", "-m", "embertide"]
