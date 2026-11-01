# HRP Digital Marketing Studio API + job runner for Cloud Run (UAT and production).
# Build from the repository root:  docker build -f backend/Dockerfile -t dm-api .
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEPLOY_MODE=cloud STORAGE_DIR=/tmp/storage PORT=8080

# Fonts for rendered images and videos (Latin + Indian scripts + emoji), tini for clean shutdowns, and the
# PostgreSQL 17 client (pg_dump for the nightly backup; as new as or newer than the database server).
RUN apt-get update && apt-get install -y --no-install-recommends \
      fonts-noto-core fonts-noto-color-emoji fonts-dejavu-core tini ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/dm
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY backend/alembic.ini backend/alembic.ini
COPY backend/alembic backend/alembic
COPY backend/app backend/app

RUN useradd --system --uid 10001 --home /srv/dm app && mkdir -p /tmp/storage && chown -R app /tmp/storage
USER app
WORKDIR /srv/dm/backend
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
