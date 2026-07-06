FROM python:3.12-slim
ARG DENO_VERSION=2.9.0
ARG RCLONE_RELEASE_VERSION=1.74.3

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
    HITOMI_BACKEND=auto \
    GALLERY_DL_AUTO_UPDATE=1 \
    GALLERY_DL_UPDATE_SPEC=gallery-dl<2.0 \
    GALLERY_DL_SLEEP_REQUEST_SECONDS=1.5 \
    MAX_CONCURRENT_DOWNLOADS=3 \
    QUEUE_PER_PROVIDER_LIMIT=1 \
    DOWNLOAD_STALL_TIMEOUT_SECONDS=600 \
    HF_XET_HIGH_PERFORMANCE=0 \
    HF_XET_NUM_CONCURRENT_RANGE_GETS=4 \
    HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY=1 \
    DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5 \
    DOWNLOAD_HTTP_MAX_RETRIES=3 \
    DOWNLOAD_RETRY_BACKOFF_SECONDS=5 \
    DOWNLOAD_MAX_RETRY_SLEEP_SECONDS=300 \
    DOWNLOAD_ENABLE_HEAD_REQUESTS=1 \
    RCLONE_CONFIG=/config/rclone/rclone.conf \
    TRANSFER_MANIFEST_DIR=/config/transfer-manifests \
    TRANSFER_MAX_CONCURRENT=1 \
    TRANSFER_DEFAULT_TRANSFERS=1 \
    TRANSFER_DEFAULT_CHECKERS=2 \
    TRANSFER_DEFAULT_BWLIMIT=40M

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg gosu \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) deno_target="x86_64-unknown-linux-gnu" ;; \
      arm64) deno_target="aarch64-unknown-linux-gnu" ;; \
      *) echo "Unsupported Deno architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${deno_target}.zip" -o /tmp/deno.zip; \
    python -m zipfile -e /tmp/deno.zip /usr/local/bin; \
    chmod +x /usr/local/bin/deno; \
    rm -f /tmp/deno.zip; \
    deno --version

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) rclone_arch="amd64" ;; \
      arm64) rclone_arch="arm64" ;; \
      *) echo "Unsupported rclone architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://downloads.rclone.org/v${RCLONE_RELEASE_VERSION}/rclone-v${RCLONE_RELEASE_VERSION}-linux-${rclone_arch}.zip" -o /tmp/rclone.zip; \
    python -m zipfile -e /tmp/rclone.zip /tmp/rclone; \
    install -m 0755 "/tmp/rclone/rclone-v${RCLONE_RELEASE_VERSION}-linux-${rclone_arch}/rclone" /usr/local/bin/rclone; \
    rm -rf /tmp/rclone /tmp/rclone.zip; \
    rclone version

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY chrome-extension ./chrome-extension
COPY app ./app

RUN mkdir -p /data /config

EXPOSE 8088

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
