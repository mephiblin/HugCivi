FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=120 \
    HF_XET_HIGH_PERFORMANCE=0 \
    HF_XET_NUM_CONCURRENT_RANGE_GETS=4 \
    HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY=1 \
    HF_SNAPSHOT_MAX_WORKERS=2 \
    DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5 \
    DOWNLOAD_HTTP_MAX_RETRIES=3 \
    DOWNLOAD_RETRY_BACKOFF_SECONDS=5 \
    DOWNLOAD_MAX_RETRY_SLEEP_SECONDS=300 \
    DOWNLOAD_ENABLE_HEAD_REQUESTS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data /config

EXPOSE 8088

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
