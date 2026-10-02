FROM python:3.12-slim

# uv installs into /app/.venv with the image Python, without the dev group and without a cache.
# uv compiles the bytecode of the packages 1 time, so a start does not compile them again.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 \
  UV_NO_DEV=1 UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY daedalus ./daedalus
COPY config ./config
# Editable install: the store path follows the source folder, so state goes to /app/.daedalus-state.
# tzdata: the TZ env var sets the local clock of the catalog schedule.
# libpcre2-8-0: the install takes the Debian revision with the fix, ahead of a base image refresh.
# pip is pinned: the image ships an old one, and nothing at start installs with it.
# uv comes from a build mount, so it is not in the image.
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.19,source=/uv,target=/bin/uv \
  apt-get update && apt-get install -y --no-install-recommends tzdata libpcre2-8-0 \
  && rm -rf /var/lib/apt/lists/* \
  && python -m pip install --no-cache-dir "pip==26.2.1" \
  && uv sync --locked \
  && useradd --uid 1000 --create-home daedalus \
  && mkdir .daedalus-state \
  && chown daedalus .daedalus-state

# The version under the logo: the v* tag, or dev-COMMIT from install --dev.
ARG VERSION=dev
ENV DAEDALUS_VERSION=$VERSION
USER daedalus
EXPOSE 3357
# The first start can build the catalog before the server listens, so the start period is long.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('DAEDALUS_PORT', '3357') + '/health', timeout=4)"]
CMD ["daedalus", "serve"]
