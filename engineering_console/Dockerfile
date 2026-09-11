FROM node:24.21.0-bookworm-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553

ENV NODE_ENV=production \
    HOME=/home/inqsi

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git tini util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 inqsi \
    && useradd --system --uid 10001 --gid inqsi --create-home --home-dir /home/inqsi inqsi

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && npm cache clean --force

COPY src ./src
COPY scripts ./scripts
RUN chmod 0755 scripts/container-entrypoint.sh \
    && chown -R inqsi:inqsi /app /home/inqsi

USER 10001:10001
ENTRYPOINT ["/usr/bin/tini","--","/app/scripts/container-entrypoint.sh"]
