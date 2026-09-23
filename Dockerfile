FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml requirements.lock README.md ./
RUN pip install -r requirements.lock

COPY bot ./bot
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-deps .

HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-m", "bot.healthcheck"]

CMD ["python", "-m", "bot"]
