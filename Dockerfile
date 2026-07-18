# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md constraints.lock ./
COPY job_hunt ./job_hunt
RUN pip install --upgrade pip \
    && pip install -c constraints.lock ".[mcp,documents,postgres]"

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/tarunlnmiit/autopilot-jobhunt" \
      org.opencontainers.image.description="Secure, user-reviewed job discovery agent" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json

RUN groupadd --system autopilot \
    && useradd --system --gid autopilot --home-dir /app --shell /usr/sbin/nologin autopilot

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY companies.json config.example.json ./
COPY resume/master_resume.json resume/master_resume.en.json ./resume/
RUN mkdir -p state output \
    && chown -R autopilot:autopilot /app

USER autopilot
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["autopilot", "web"]
