FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml ./
COPY daedalus ./daedalus
COPY config ./config
# Editable install: the store path follows the source folder, so state goes to /app/.daedalus-state.
# tzdata: the TZ env var sets the local clock of the catalog schedule.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
  && rm -rf /var/lib/apt/lists/* \
  && pip install -e . \
  && useradd --uid 1000 --create-home daedalus \
  && mkdir .daedalus-state \
  && chown daedalus .daedalus-state

USER daedalus
EXPOSE 3357
# The first start can build the catalog before the server listens, so the start period is long.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('DAEDALUS_PORT', '3357') + '/health', timeout=4)"
CMD ["daedalus", "serve"]
