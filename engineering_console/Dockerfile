FROM node:20-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates git tini \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --ignore-scripts --no-audit --no-fund
COPY src ./src
COPY docker-entrypoint-worker.sh /usr/local/bin/inqsi-worker-entrypoint
RUN chmod 0755 /usr/local/bin/inqsi-worker-entrypoint \
  && mkdir -p /var/lib/inqsi \
  && chown -R node:node /app /var/lib/inqsi

ENV NODE_ENV=production
USER node
EXPOSE 8787
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/inqsi-worker-entrypoint"]
