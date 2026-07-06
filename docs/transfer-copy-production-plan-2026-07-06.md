# 복사 전용 전송 제작 계획

작성일: 2026-07-06

상태: 기본 구현 완료. 이 문서는 `docs/transfer-design-2026-07-02.md`를 복사 전용 제품 범위로 좁혀 실제 구현 순서를 정리한 기록이다.

## 결론

HugCivi 전송 기능은 `copy`만 지원한다.

초기 제품 목표는 NAS의 HugCivi `/data` archive에서 선택한 파일이나 폴더를 등록된 외부 대상, 특히 내부망 PC의 ComfyUI 모델 폴더로 복사하는 것이다. 로컬 원본과 원격 대상에 있는 추가 파일은 삭제하지 않는다.

초기 추천 경로:

```text
/data/stable-diffusion/checkpoints
  -> rclone SMB remote
  -> PC ComfyUI/models/checkpoints
```

## 제품 제약

- 허용: `copy`와 단일 파일용 `copyto`.
- 금지: `sync`, `move`, remote delete, delete-before, delete-during, delete-excluded.
- 금지: HugCivi 자체를 인터넷 파일 서버로 공개.
- 금지: rclone serve 자동 실행.
- 금지: job 생성 시 임의 host, IP, URL, raw remote string 입력.
- 금지: 일반 전송 job에서 `/data` root 전체 전송.
- 예외: settings-pane 전용 `/data` root clone은 `local_mount` target만 허용하고 browser-provided `source_path` 없이 서버가 source를 고정한다.
- 필수: 등록된 target id만 사용.
- 필수: source path는 기존 `/data` 안전 helper로 검증.
- 필수: target별 allowed source prefix를 강제.
- 필수: 전송 subprocess는 shell string이 아니라 argv list로 실행.

이 제약은 구현, UI, API, 테스트 모두에서 불변 조건으로 취급한다. 나중에 sync나 move가 필요해져도 이 기능에 섞지 않고 별도 설계와 별도 위험 승인을 요구한다.

## 구현 방식

전송은 외부 다운로드가 아니라 서버-local outbound 작업이다.

```text
Browser
  -> FastAPI transfer API in app/main.py
      -> transfer target state in app/db.py
      -> /data source path validation
      -> app/transfer.py rclone command builder/helpers
      -> app/main.py transfer_copy runner
      -> app/internal_jobs.py job scheduler
      -> jobs row with job_kind='transfer_copy'
```

`app/downloader.py`에는 넣지 않는다. 다운로드 provider queue와 전송 queue는 성격이 다르고, 전송은 이미 `/data`에 있는 파일을 서버가 복사하는 내부 작업이다.

## 1차 대상

MVP target은 PC ComfyUI checkpoints이다.

```json
{
  "name": "PC ComfyUI Checkpoints",
  "remote_name": "pc-comfyui",
  "remote_path": "ComfyUI/models/checkpoints",
  "enabled": true,
  "policy": {
    "bwlimit": "40M",
    "transfers": 1,
    "checkers": 2,
    "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
    "include_patterns": ["*.safetensors", "*.ckpt"],
    "preserve_folder_name": true,
    "require_check": false
  }
}
```

PC 쪽 권장 운영 형태:

```text
Windows folder: D:\ComfyUI\models
SMB share: ComfyUI
Dedicated account: hugcivi_transfer
Permission: write under ComfyUI/models
PC IP: DHCP reservation or static IP
```

rclone config는 `/config/rclone/rclone.conf`에 둔다. DB에는 원격 credential 원문을 저장하지 않는다.

## DB 제작 범위

`app/db.py`에 additive migration으로 `transfer_targets`를 추가한다.

```text
id INTEGER PRIMARY KEY
name TEXT NOT NULL
remote_name TEXT NOT NULL
remote_path TEXT NOT NULL DEFAULT ''
enabled INTEGER NOT NULL DEFAULT 1
policy_json TEXT NOT NULL DEFAULT '{}'
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

`mode` 컬럼은 초기 DB 모델에 넣지 않는다. 제품 범위가 copy only라서 다른 mode를 저장할 이유가 없다. 나중에 호환성 때문에 필요해져도 값은 `copy`만 허용하고 API/UI에서는 노출하지 않는다.

전송 실행은 기존 `jobs` table을 쓴다.

```text
job_kind = 'transfer_copy'
source = 'transfer'
status = queued/running/paused/done/failed/canceled
target_dir = local source path
parsed_json.payload = transfer request snapshot
artifact_path = optional manifest path
```

현재 `db.create_internal_job()`는 `source='internal'`을 고정한다. 작업 목록에서 전송 필터와 표시를 깔끔하게 하려면 선택 인자 `source: str = "internal"`을 추가하고, `transfer_copy` 생성 시 `source="transfer"`를 넘긴다.

## API 제작 범위

초기 API:

```text
GET    /api/transfer/targets
POST   /api/transfer/targets
PATCH  /api/transfer/targets/{target_id}
DELETE /api/transfer/targets/{target_id}
POST   /api/transfer/jobs
POST   /api/transfer/preflight
```

`POST /api/transfer/jobs` 예:

```json
{
  "target_id": 1,
  "source_path": "stable-diffusion/checkpoints/SomeModel.safetensors",
  "destination_subpath": ""
}
```

API는 `mode`를 받지 않는다. 내부 payload에도 `mode`를 저장하지 않는다. rclone 명령은 항상 copy 계열로만 만든다.

Target validation:

- `remote_name`: `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`
- `remote_path`: absolute path 금지, `..` 금지, backslash를 slash로 정규화.
- `policy_json`: 알려진 key만 수용하고 나머지는 무시하거나 400.
- `bwlimit`, `transfers`, `checkers`: conservative clamp 적용.
- `include_patterns`: glob include만 허용하고 exclude/delete flag로 변환하지 않는다.

Job validation:

- `source_path`는 `existing_data_path()`로 존재 확인.
- `ensure_downloadable_path()`로 `/data` root 전체 전송 차단.
- symlink ancestor 또는 symlink escape는 거부.
- source relative path가 target policy의 `allowed_source_prefixes` 중 하나 아래인지 확인.
- `destination_subpath`는 optional이며 target base path 밖으로 나가지 못한다.

## rclone 실행 제작 범위

새 모듈 `app/transfer.py`를 둔다.

주요 함수:

```python
def normalize_remote_path(value: str) -> str: ...
def validate_transfer_target_payload(payload: dict) -> dict: ...
def resolve_transfer_source(source_path: str, target: dict) -> Path: ...
def resolve_transfer_destination(source: Path, target: dict, payload: dict) -> str: ...
def build_rclone_copy_command(source: Path, destination: str, policy: dict) -> list[str]: ...
def run_rclone_copy_job(job_id: int, job: dict) -> None: ...
```

Command rules:

- 파일 선택: `rclone copyto <source-file> <remote>:<base>/<filename>`
- 폴더 선택: `rclone copy <source-folder> <remote>:<base>/<source-folder-name>`
- `--config /config/rclone/rclone.conf`
- `--transfers`, `--checkers`, `--bwlimit`는 target policy에서만 생성.
- `--include`는 target policy의 include pattern에서만 생성.
- 마지막에는 `--exclude *`를 붙여 include 대상 외 파일을 보내지 않는다.
- 삭제 관련 flag는 생성하지 않고, 사용자가 넘길 수 있는 API도 없다.
- stdout/stderr는 `redact_sensitive_text()`를 거친 뒤 job log에 축약 저장.
- 작업 로그는 SQLite job log에 남기고, 완료 manifest는 `/config/transfer-manifests` 아래 artifact로 기록한다.

Progress는 rclone stats output을 best-effort로 파싱한다. 첫 버전은 정확한 ETA보다 상태, 전송량, 마지막 로그를 안정적으로 보여주는 것을 우선한다.

Pause/cancel:

- handler loop에서 `internal_jobs.check_job_control(job_id)`를 주기적으로 호출한다.
- pause/cancel/deleting 상태가 감지되면 rclone process를 terminate한다.
- resume은 같은 copy 명령을 다시 실행한다. rclone copy는 이미 전송된 동일 파일을 비교해 건너뛸 수 있으므로 byte-level resume을 직접 구현하지 않는다.

## Docker와 운영 설정

Docker image에 rclone을 포함한다.

권장 방식:

- `ARG RCLONE_VERSION=<pinned-version>`
- Debian architecture를 rclone release architecture로 매핑.
- 공식 release zip을 받아 `/usr/local/bin/rclone`에 설치.
- install script보다 pinned release를 우선한다.

추가 env:

```text
RCLONE_CONFIG=/config/rclone/rclone.conf
TRANSFER_MANIFEST_DIR=/config/transfer-manifests
TRANSFER_MAX_CONCURRENT=1
TRANSFER_DEFAULT_TRANSFERS=1
TRANSFER_DEFAULT_CHECKERS=2
TRANSFER_DEFAULT_BWLIMIT=40M
```

초기 구현은 `INTERNAL_JOB_MAX_CONCURRENT` 아래에서 실행하되, transfer handler 내부에 별도 semaphore를 둬 `TRANSFER_MAX_CONCURRENT=1`을 강제한다. ZIP/transcode와 완전히 분리된 scheduler가 필요해지는지는 실제 사용 후 판단한다.

## UI 제작 범위

설정 modal에 `전송 대상` pane을 추가한다.

초기 UI 필드:

- 표시 이름
- rclone remote 이름
- remote base path
- 허용 source prefix
- include pattern
- bandwidth limit
- transfers/checkers
- 활성 여부

rclone remote 생성 wizard는 만들지 않는다. 사용자는 `/config/rclone/rclone.conf`를 운영자가 준비한 뒤, HugCivi UI에는 등록된 remote name과 정책만 저장한다.

보관함 카드와 폴더 context menu에 `전송` action을 추가한다. 모달은 target, source, destination preview, include policy, 예상 파일 수/크기를 보여주고 `전송 큐에 추가`만 제공한다. `이동`, `동기화`, `대상 삭제` 같은 문구나 control은 넣지 않는다.

작업 목록은 기존 job list를 재사용한다. `source='transfer'` 또는 `job_kind='transfer_copy'`는 `Transfer`로 표시하고, pause/resume/delete는 existing job action route를 통과시킨다.

## 구현 순서

1. rclone runtime
   - Dockerfile에 pinned rclone 설치 추가.
   - `RCLONE_CONFIG`와 transfer 기본 env를 `docs/configuration.md`, `portainer-stack.yml`에 반영.
   - `/config/rclone` 운영 경로를 `docs/operations.md`에 문서화.

2. DB와 target API
   - `transfer_targets` additive migration.
   - target CRUD helpers.
   - target API route와 validation.
   - `db.create_internal_job(source=...)` optional parameter 추가.

3. transfer core
   - `app/transfer.py` 추가.
   - `INTERNAL_JOB_TRANSFER_COPY = "transfer_copy"` 정의.
   - `register_internal_job_handlers()`에 handler 등록.
   - transfer preflight와 job 생성 API 추가.
   - mocked subprocess 기반 성공/실패/progress/pause/cancel 테스트.

4. UI
   - settings pane 추가.
   - context menu transfer action 추가.
   - transfer modal/preflight/job submit/polling 추가.
   - 모바일 카드 action에도 동일 경로 추가.

5. 문서와 운영
   - README에 사용자-facing 전송 절차 추가.
   - `docs/feature-code-map.md`에 Transfer row 추가.
   - `docs/architecture.md` internal job 종류와 API group 업데이트.
   - `docs/configuration.md`, `docs/operations.md`, patch notes 업데이트.

6. 배포
   - targeted tests와 full pytest.
   - `SKILL_Dev/skill_build.md` 흐름으로 image build/push.
   - Portainer pull 후 rclone version과 target creation smoke 확인.

## 테스트 계획

새 테스트 파일은 `tests/test_transfer_api.py`, `tests/test_transfer_core.py`, `tests/test_transfer_db.py`로 나눈다.

필수 backend tests:

- `transfer_targets` migration 생성.
- target create/list/update/delete.
- disabled target job 생성 거부.
- raw remote string이나 host/IP를 job API가 받지 않음.
- source path `..` escape 거부.
- `/data` root 전송 거부.
- symlink ancestor와 symlink escape 거부.
- allowed source prefix 밖 source 거부.
- destination subpath `..`, absolute path, backslash escape 거부.
- copy-only API가 `mode`, `sync`, `move`, delete 관련 값을 거부.
- file source는 `rclone copyto` argv list를 생성.
- folder source는 `rclone copy` argv list를 생성.
- `.safetensors`/`.ckpt` include와 final `--exclude *` 생성.
- subprocess success는 job done과 artifact/content ref 기록.
- subprocess failure는 job failed와 redacted log 기록.
- pause/cancel/deleting은 process terminate와 status transition 기록.

필수 UI/template tests:

- context menu에 `전송` action이 렌더링됨.
- transfer modal target/source/destination fields가 존재함.
- JS job submit payload에 raw remote나 mode가 포함되지 않음.
- job list label이 `transfer_copy`를 표시할 수 있음.

필수 regression tests:

- `tests/test_review_fixes.py`의 filesystem safety tests 유지.
- `test_internal_job_rows_are_separate_from_download_resume_list` 계열에 `transfer_copy` 추가.
- settings credential visibility/redaction tests에 rclone credential이 DB/API/job log로 새지 않음을 추가.

## 수용 기준

MVP 완료 조건:

- 컨테이너 안에서 `rclone version`이 동작한다.
- `/config/rclone/rclone.conf`의 SMB remote를 이용해 테스트 파일을 PC 공유 폴더로 복사할 수 있다.
- UI에서 `/data/stable-diffusion/checkpoints/*.safetensors` 파일을 선택해 PC ComfyUI target으로 전송 job을 만들 수 있다.
- 동일 job 재실행 시 기존 대상 파일을 삭제하지 않고 rclone copy 비교에 맡긴다.
- `/data` root, 허용 prefix 밖 파일, symlink escape, raw remote 입력, copy 외 mode는 모두 거부된다.
- job log와 API response에 rclone credential이 노출되지 않는다.
- `git diff --check`, targeted pytest, JS syntax checks, full pytest가 통과한다.

## 후속 확장

MVP 이후 같은 copy-only 원칙으로 확장한다.

- LoRA: `stable-diffusion/loras` -> `ComfyUI/models/loras`
- VAE: `stable-diffusion/vae` -> `ComfyUI/models/vae`
- Embeddings: `stable-diffusion/embeddings` -> `ComfyUI/models/embeddings`
- ControlNet: `stable-diffusion/controlnet` -> `ComfyUI/models/controlnet`
- Upscalers: `stable-diffusion/upscalers` -> `ComfyUI/models/upscale_models`
- 다운로드 완료 후 자동 copy job 생성.
- 야간 window.
- optional `rclone check`, 단 이것도 삭제 없는 검증만 허용.

자동 전송은 Civitai/Hugging Face download handler 내부에 직접 rclone을 넣지 않는다. 다운로드 완료 후 별도의 `transfer_copy` internal job을 생성하는 방식으로 연결한다.

## `/data_remote` 후속 방향

Receiver 없이 내부망 PC/Synology/원격 공유 폴더를 전송 대상으로 쓰는 방향은 별도 후속 설계인 [`/data_remote` Connected Transfer Design 2026-07-06](data-remote-transfer-design-2026-07-06.md)를 기준으로 destination-only `local_mount` baseline까지 구현됐다.

핵심 차이:

- 이 문서의 구현 baseline은 rclone/Receiver target으로 "내 `/data` 파일을 등록된 대상에게 보내는 copy-only outbound 작업"이다.
- `/data_remote` 구현은 host가 이미 마운트한 PC/Synology/원격 공유 폴더를 Docker에 `/data_remote/<target>`으로 연결하고, HugCivi가 이를 `local_mount` transfer target으로만 다루는 작업이다.
- `/data_remote`는 보관함이 아니며 라이브러리 index, rename, move, delete UI에 섞지 않는다.
- 세 방식 모두 sync/move/delete를 금지하고, 일반 전송 source는 `/data` 내부 path로 제한한다. `/data` root clone은 `local_mount` 전용 settings flow에서만 허용한다.
