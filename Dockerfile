ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    PORT=8585 \
    SHELFLIFE_VERSION=${VERSION}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY entrypoint.sh /entrypoint.sh

LABEL org.opencontainers.image.title="Shelflife" \
      org.opencontainers.image.description="Find the biggest, least-watched titles in your Plex libraries using Tautulli history" \
      org.opencontainers.image.source="https://github.com/au12/shelflife" \
      org.opencontainers.image.version="${VERSION}"

VOLUME /config
EXPOSE 8585
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8585\")}/healthz',timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
