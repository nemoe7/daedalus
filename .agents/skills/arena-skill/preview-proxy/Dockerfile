# Arena preview proxy: one small Node image, no build step, no secrets baked in.
FROM node:22-alpine

WORKDIR /app

# The server is standard-library Node, so the source tree is the whole build.
COPY server.js /app/server.js
COPY assets/ /app/assets/

RUN adduser -D -H extension
USER extension

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

CMD ["node", "server.js"]
