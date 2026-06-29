# NAS Model Archiver

Synology NAS에서 Hugging Face / Civitai / 일반 URL 모델 파일을 **사용 목적이 아닌 보관 목적**으로 다운로드하는 간단한 웹 UI + Docker 앱입니다.

## 기능

- 웹 UI에서 URL 또는 안전한 CLI 형태 입력
- Hugging Face 모델/데이터셋/스페이스 전체 또는 파일 다운로드
- Civitai 모델 페이지 URL, modelVersionId, API 다운로드 URL 처리
- 일반 HTTP/HTTPS 파일 URL 다운로드
- `/data` 폴더 트리 조회와 하위 폴더 생성
- Hugging Face / Civitai 메타데이터 기반 자동 분류
- Civitai 이미지 썸네일, 모델 타입, 베이스 모델, 파일 포맷, 정밀도 정보 표시
- SQLite 기반 작업 기록
- 다운로드 진행률 표시
- 토큰은 환경변수 또는 웹 UI에서 설정: `HF_TOKEN`, `CIVITAI_TOKEN`
- Hugging Face 토큰은 인증/게이트 모델/rate limit 완화에 사용하고, `hf_xet` 고성능 전송 모드로 대용량 파일 다운로드를 가속
- 웹 UI Basic Auth 지원

## Synology 설치 개요

1. NAS에 공유 폴더 생성 예: `/volume1/AI_MODELS`
2. Container Manager > Project > Create에서 이 폴더 또는 `/volume1/docker/nas-model-archiver`에 `docker-compose.yml` 업로드/작성
3. 아래 Compose의 볼륨 경로를 NAS 경로에 맞게 수정
4. 프로젝트 실행 후 `http://NAS_IP:8088` 접속

## docker-compose.yml 예시

```yaml
services:
  nas-model-archiver:
    build: .
    container_name: nas-model-archiver
    restart: unless-stopped
    ports:
      - "8088:8088"
    environment:
      APP_USERNAME: "admin"
      APP_PASSWORD: "replace-with-a-strong-password"
      HF_TOKEN: ""
      CIVITAI_TOKEN: ""
      MAX_CONCURRENT_DOWNLOADS: "1"
      HF_HUB_DOWNLOAD_TIMEOUT: "120"
      HF_XET_HIGH_PERFORMANCE: "1"
      HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY: "1"
      HF_SNAPSHOT_MAX_WORKERS: "8"
    volumes:
      - /volume1/AI_MODELS:/data
      - /volume1/docker/nas-model-archiver/config:/config
```

## 지원 입력 예시

### Hugging Face

```text
https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
https://huggingface.co/datasets/bigcode/the-stack
https://huggingface.co/openai-community/gpt2/resolve/main/config.json
hf download openai-community/gpt2 --include "*.json" --exclude "*.msgpack"
hf download hf://datasets/bigcode/the-stack@v1.1
```

### Civitai

```text
https://civitai.com/models/123456/model-name?modelVersionId=456789
https://civitai.com/api/download/models/456789
456789
```

숫자만 넣으면 Civitai model version ID로 간주합니다.

## 자동 분류

저장 하위 폴더를 비워두면 API 메타데이터를 기준으로 자동 저장 경로를 나눕니다.

```text
/data/huggingface/llm/...
/data/huggingface/embeddings/...
/data/huggingface/image/checkpoints/...
/data/civitai/checkpoints/sdxl-1.0/...
/data/civitai/loras/sd-1.5/...
/data/civitai/embeddings/...
```

직접 폴더를 선택하거나 입력하면 해당 경로를 우선 사용합니다.

## 보안 주의

- 웹에서 임의의 셸 명령을 실행하지 않습니다. `hf download ...` 형태는 안전 파서로 해석만 합니다.
- 외부 인터넷에 공개하지 않는 것을 권장합니다.
- `APP_PASSWORD` 기본 예시값은 반드시 강한 비밀번호로 바꾸세요. 예시값 그대로 두면 앱이 로그인을 차단합니다.
- 토큰은 Synology Secret/환경변수 관리가 가장 안전합니다. 웹 UI로 입력한 토큰은 `/config/jobs.sqlite3`에 저장되므로 `/config` 폴더 권한을 제한하세요.
- 웹 UI로 토큰을 저장하면 환경변수보다 UI 저장값을 우선 사용합니다. 입력칸을 비워 저장하면 기존 값은 유지됩니다.
- 작업 목록, 로그, 메타데이터에는 토큰성 쿼리값을 마스킹해서 저장합니다. 단, 재시작 후 다운로드 재개를 위해 내부 작업 payload에는 실제 다운로드 URL이 남을 수 있으므로 `/config` 폴더 접근 권한도 제한하세요.
- 모델 라이선스와 각 사이트의 이용약관을 확인하고 보관하세요.

## 진행률 참고

- Civitai와 일반 URL 다운로드는 스트리밍 다운로드 중 진행률을 갱신합니다.
- Hugging Face 전체 저장소 다운로드는 `huggingface_hub` 내부 다운로드에 맡기므로 파일 단위 완료 로그 중심으로 표시됩니다.

## 개발 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```
