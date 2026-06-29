FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBRARY_ACTIVE=ComfyUI \
    ROUTE_LLM_ROOT=huggingface/llm \
    ROUTE_LORA_ROOT=stable-diffusion/loras \
    ROUTE_CHECKPOINT_ROOT=stable-diffusion/checkpoints \
    ROUTE_DIFFUSION_MODEL_ROOT=stable-diffusion/diffusion_models \
    ROUTE_EMBEDDING_ROOT=stable-diffusion/embeddings \
    ROUTE_VAE_ROOT=stable-diffusion/vae \
    ROUTE_CONTROLNET_ROOT=stable-diffusion/controlnet \
    ROUTE_UPSCALER_ROOT=stable-diffusion/upscalers \
    MAX_CONCURRENT_DOWNLOADS=2 \
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
