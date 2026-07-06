# `/data_remote` Connected Transfer Design

작성일: 2026-07-06

상태: implemented baseline. 현재 구현된 범위는 destination-only `local_mount` transfer target, `/data_remote` target-relative folder tree, preflight writable/offline checks, temp-file local copy, skip-existing behavior, settings-pane `/data` root clone to local mount, and transfer manifests이다. read-only import, friend-library UX, mount management, sync/move/delete는 구현하지 않았다.

## 결론

HugCivi의 다음 전송 UX는 `Receiver`나 친구 HugCivi 직접 API보다 먼저 `/data_remote`라는 연결 폴더 구간을 두고, 그 아래에 운영자가 마운트한 PC/Synology/원격 공유 폴더를 copy-only 전송 대상으로 쓰는 방향이 가장 실용적이다.

핵심 모델:

```text
/data
  HugCivi의 진짜 보관함. 라이브러리 index, 카드, 다운로드 결과, 파일 관리의 기준.

/data_remote
  HugCivi가 쓰거나 읽을 수 있는 연결된 복사 대상 영역.
  라이브러리로 자동 인덱싱하지 않고, 전송 target picker에서만 노출한다.
```

권장 MVP:

```text
Synology/host
  -> PC SMB share, 다른 Synology remote folder, 외장/공유 폴더들을 host에 mount
  -> Docker에 /data_remote/<target-name> 하위 여러 연결 폴더로 bind mount

HugCivi
  -> 연결 폴더마다 transfer target kind=local_mount 등록
  -> 우클릭 전송
  -> 선택한 target의 /data_remote/<target-name> 아래 folder tree 선택
  -> /data source를 copy-only job으로 복사
```

이 방식은 위험을 완전히 없애지는 않지만 위험의 모양이 단순하다. HugCivi 컨테이너가 `/data_remote`에 마운트된 범위에는 쓸 수 있다. 따라서 운영자는 `/data_remote`에 필요한 하위 폴더만 좁게 마운트해야 한다.

## 제품 판단

`/data_remote`는 세련된 "친구 라이브러리" 기능은 아니지만, HugCivi의 현재 제품 단계에서는 가장 적은 코드와 가장 명확한 운영 모델로 내부망/개인 장비 전송 문제를 해결한다.

장점:

- Receiver 앱 없이 PC 또는 Synology 폴더로 복사할 수 있다.
- rclone credential과 remote 설정을 모르는 사용자도 Docker/Synology mount만 이해하면 된다.
- HugCivi backend는 local filesystem copy로 처리할 수 있어 failure mode가 단순하다.
- Synology File Station의 remote folder mount, Linux CIFS/NFS mount, Docker bind mount 같은 기존 운영 방식을 그대로 활용한다.
- 내부망 PC ComfyUI 폴더, 다른 Synology 공유 폴더, 임시 친구 drop folder를 같은 UX로 다룰 수 있다.

단점:

- mount 범위가 넓으면 HugCivi가 그만큼 쓸 수 있다.
- remote mount가 끊기면 전송 job이 실패한다.
- mount credential과 network filesystem 안정성은 HugCivi 밖의 운영 책임이다.
- 친구와 인터넷 경유 공유까지 깔끔하게 해결하지는 않는다. 그 경우에는 Synology Drive/ShareSync mailbox나 별도 encrypted capsule 설계가 필요하다.

## 현재 기능과의 관계

현재 전송 target:

| Target kind | 현재 상태 | 역할 |
| --- | --- | --- |
| `rclone` | implemented | 범용 remote, SMB/WebDAV/SFTP/cloud 등 운영자 관리 원격지. |
| `receiver` | implemented | PC에 HugCivi Receiver를 띄워 HTTP로 수신하고 수신 UI를 보여주는 방식. |
| `local_mount` | implemented baseline | `/data_remote` 아래 이미 마운트된 폴더를 copy-only 대상처럼 쓰는 방식. |

관계:

- `local_mount`는 내부망과 개인 장비에서 1순위 권장 target이 된다.
- `receiver`는 PC에 SMB 공유를 열기 싫거나, 수신 상태 UI가 필요할 때 남긴다.
- `rclone`은 외부망, cloud, 특수 remote, 운영자 주도 고급 설정용으로 남긴다.
- 세 방식 모두 copy-only여야 하며 sync/move/delete를 포함하지 않는다.

## `/data_remote` 경계

`/data_remote`는 `/data`와 같은 레벨의 별도 Docker mount root다.

예시 compose:

```yaml
services:
  hugcivi:
    volumes:
      - /volume1/hugcivi/data:/data
      - /volume1/hugcivi/config:/config
      - /volume1/hugcivi/remotes:/data_remote
```

예시 host layout:

```text
/volume1/hugcivi/remotes/
  pc-comfyui/
    checkpoints/
    loras/
  win-documents/
  friend-drop/
  studio-nas/
  backup-disk/
```

또는 Synology가 remote folder를 host 쪽에 마운트한 뒤:

```text
/volume1/hugcivi/remotes/pc-comfyui
  -> mounted remote CIFS folder: //PC/ComfyUI/models

/volume1/hugcivi/remotes/friend-drop
  -> mounted remote Synology folder: //friend-nas/HugCiviDrop

/volume1/hugcivi/remotes/studio-nas
  -> mounted remote CIFS folder: //studio-nas/Models
```

HugCivi 내부에서는 항상 `/data_remote/<target-name>/...`로만 본다. 사용자가 UI에서 host absolute path, SMB URL, IP, credential을 입력하지 않는다.

여러 remote folder를 지정하는 것은 기본 전제다. 권장 형태는 Docker에는 `/volume1/hugcivi/remotes:/data_remote` 같은 상위 폴더 하나를 물리고, 그 아래 direct child를 여러 연결 폴더로 관리하는 것이다.

```text
/data_remote/
  pc-comfyui/
    checkpoints/
    loras/
  win-documents/
  friend-drop/
  studio-nas/
```

각 연결 폴더 또는 그 하위 폴더는 독립적인 `local_mount` target이 될 수 있다.

```text
PC ComfyUI Checkpoints -> /data_remote/pc-comfyui/checkpoints
PC ComfyUI LoRA        -> /data_remote/pc-comfyui/loras
Windows Documents      -> /data_remote/win-documents
Friend Drop            -> /data_remote/friend-drop
Studio NAS Models      -> /data_remote/studio-nas/models
```

Docker bind mount를 여러 개 직접 지정하는 방식도 가능하다.

```yaml
services:
  hugcivi:
    volumes:
      - /volume1/hugcivi/data:/data
      - /volume1/hugcivi/config:/config
      - /volume1/hugcivi/remotes/pc-comfyui:/data_remote/pc-comfyui
      - /volume1/hugcivi/remotes/win-documents:/data_remote/win-documents
      - /volume1/hugcivi/remotes/friend-drop:/data_remote/friend-drop
```

다만 운영 단순성은 상위 `/data_remote` 하나를 Docker에 물리고 host/Synology에서 하위 remote mount를 관리하는 방식이 더 좋다.

## 안전 불변 조건

필수:

- `/data_remote`는 라이브러리 index 대상으로 자동 포함하지 않는다.
- `/data_remote`는 파일관리 메뉴의 rename/move/delete 대상이 아니다.
- 일반 전송 source는 여전히 `/data` 내부 existing path만 허용하고 `/data` root는 금지한다.
- `/data` root 전체 복제는 설정 화면의 전용 local mount clone endpoint에서만 허용하며, browser-provided `source_path`를 받지 않는다.
- 전송 destination은 등록된 `local_mount` target의 base path 아래만 허용한다.
- `/data_remote` root 전체를 target으로 등록하지 않는다.
- target base path는 `/data_remote` 하위 direct child 또는 그 아래 좁은 폴더여야 한다.
- 여러 연결 폴더를 등록할 수 있지만 target별 base path, allowed source prefix, include pattern, offline/writable preflight는 서로 분리한다.
- `..`, absolute path, backslash escape, symlink escape를 거부한다.
- symlink directory traversal은 따라가지 않는다.
- mount 내부에서 발견한 symlink는 기본적으로 표시하지 않거나 leaf로만 처리한다.
- job API에서 임의 host, IP, SMB URL, raw mount string을 받지 않는다.
- copy-only만 허용한다. sync/move/delete/mirror는 계속 금지한다.

권장:

- mount별 allowed source prefix를 유지한다.
- mount별 include pattern을 유지한다.
- 파일 크기와 파일 수 preflight를 수행한다.
- target 오프라인 여부를 preflight에서 빠르게 감지한다.
- mount가 read-only면 UI에 "읽기 전용 대상"으로 표시하고 전송 job 생성을 막는다.
- root-owned 또는 permission denied destination은 preflight에서 실패시킨다.

## DB/API 초안

기존 `transfer_targets`에 `kind='local_mount'`를 추가하는 방식이 가장 작다.

추가/재사용 필드:

```text
kind TEXT NOT NULL DEFAULT 'rclone'
remote_name TEXT
remote_path TEXT NOT NULL DEFAULT ''
policy_json TEXT NOT NULL DEFAULT '{}'
```

`local_mount` 의미:

| Field | 의미 |
| --- | --- |
| `kind` | `local_mount` |
| `remote_name` | 비워두거나 UI display용으로만 사용. filesystem resolution에 쓰지 않음. |
| `remote_path` | `/data_remote` 기준 상대 base path. 예: `pc-comfyui/checkpoints`. |
| `policy_json.allowed_source_prefixes` | `/data` source 허용 prefix. |
| `policy_json.include_patterns` | 전송 허용 glob. |
| `policy_json.preserve_folder_name` | 폴더 source 복사 시 source folder name 유지 여부. |

API는 기존 endpoint를 유지한다.

```text
GET    /api/transfer/targets
POST   /api/transfer/targets
PATCH  /api/transfer/targets/{target_id}
DELETE /api/transfer/targets/{target_id}
GET    /api/transfer/targets/{target_id}/local-mount/tree
POST   /api/transfer/preflight
POST   /api/transfer/jobs
POST   /api/transfer/data-root/preflight
POST   /api/transfer/data-root/jobs
```

새 tree endpoint는 `/data_remote`의 token이 필요 없는 local filesystem browse다. 그래도 browser에는 `/data_remote` absolute path를 주지 않고 target-relative path만 준다.

예시 payload:

```json
{
  "name": "PC ComfyUI Checkpoints",
  "kind": "local_mount",
  "remote_path": "pc-comfyui/checkpoints",
  "enabled": true,
  "policy": {
    "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
    "include_patterns": ["*.safetensors", "*.ckpt"],
    "preserve_folder_name": true
  }
}
```

여러 연결 폴더를 등록하는 target 예:

```json
[
  {
    "name": "PC ComfyUI Checkpoints",
    "kind": "local_mount",
    "remote_path": "pc-comfyui/checkpoints",
    "policy": {
      "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
      "include_patterns": ["*.safetensors", "*.ckpt"]
    }
  },
  {
    "name": "PC ComfyUI LoRA",
    "kind": "local_mount",
    "remote_path": "pc-comfyui/loras",
    "policy": {
      "allowed_source_prefixes": ["stable-diffusion/loras"],
      "include_patterns": ["*.safetensors", "*.pt"]
    }
  },
  {
    "name": "Friend Drop",
    "kind": "local_mount",
    "remote_path": "friend-drop",
    "policy": {
      "allowed_source_prefixes": ["stable-diffusion", "gallery-dl"],
      "include_patterns": ["*.safetensors", "*.ckpt", "*.json", "*.jpg", "*.png", "*.mp4", "*.webm"]
    }
  }
]
```

전송 job payload는 현재 Receiver와 같은 shape를 유지한다.

```json
{
  "target_id": 3,
  "source_path": "stable-diffusion/checkpoints/ModelA.safetensors",
  "destination_subpath": ""
}
```

`/data` root clone job payload는 서버 내부에서만 source를 고정한다.

```json
{
  "target_id": 3,
  "destination_subpath": "backup/latest",
  "data_root_clone": true
}
```

## 실행 방식

`local_mount` job은 rclone subprocess가 아니라 Python filesystem copy helper로 시작하는 것이 자연스럽다.

파일 source:

```text
/data/<source-file>
  -> /data_remote/<target-base>/<destination_subpath>/<filename>
```

폴더 source:

```text
/data/<source-folder>
  -> /data_remote/<target-base>/<destination_subpath>/<source-folder-name>
```

`/data` root clone:

```text
/data/*
  -> /data_remote/<target-base>/<destination_subpath>/*
```

복사 규칙:

- destination parent가 없으면 생성한다.
- 파일은 `.part.<job-id>` 또는 임시 파일로 쓴 뒤 완료 시 rename한다.
- 이미 같은 파일이 있으면 기본은 skip 또는 keep-both 중 하나를 정책으로 둔다.
- 첫 MVP는 `skip_existing`을 기본값으로 한다.
- 동일 파일 판단은 크기+mtime보다 보수적으로 하고, 확실하지 않으면 덮어쓰지 않는다.
- include pattern은 rclone target과 같은 의미로 적용한다.
- permission denied, no space left, read-only filesystem은 job failed로 기록한다.
- partial 파일 cleanup은 delete/clear-history 경로에 연결한다.
- job log와 manifest에는 `/data` relative source와 `/data_remote` target-relative destination만 기록한다.

## UI

설정 modal의 transfer target kind:

```text
연결 폴더 (/data_remote)
HugCivi Receiver
rclone remote
```

`local_mount` target 편집 필드:

- 표시 이름.
- 연결 폴더 base path.
- 허용 source prefix.
- include pattern.
- folder name 보존 여부.
- 활성 여부.

설정 UI는 여러 `local_mount` target을 만들 수 있어야 한다. 같은 `/data_remote` direct child 아래의 서로 다른 하위 폴더도 별도 target으로 등록할 수 있다. 예를 들어 `pc-comfyui/checkpoints`와 `pc-comfyui/loras`는 같은 PC mount를 공유하지만 전송 source prefix와 include pattern이 다르므로 별도 target으로 두는 편이 안전하다.

전송 modal:

- target 선택.
- `local_mount` target이면 `/data_remote/<target-base>` 아래 tree를 보여준다.
- 사용자는 `/data_remote`라는 시스템 경로를 직접 보지 않고, 대상 이름과 하위 폴더만 본다.
- destination preview는 `PC ComfyUI Checkpoints/checkpoints/...`처럼 product-facing 이름으로 표시한다.
- rclone/Receiver와 동일하게 `전송 큐에 추가`만 제공한다.

문구:

- `연결 폴더`
- `외부 마운트`
- `복사 대상`
- `연결된 PC/NAS 폴더`

피할 문구:

- `동기화`
- `이동`
- `미러링`
- `삭제`
- `원격 파일관리`

## Synology 운영 예시

Synology에서 PC 또는 다른 NAS 공유 폴더를 File Station remote folder로 마운트할 수 있다. 공식 DSM 문서의 흐름은 File Station에서 `Tools > Mount Remote Folder > CIFS Shared Folder`로 CIFS shared folder를 지정하는 방식이다.

권장 구조:

```text
Synology shared folder:
  /volume1/hugcivi/remotes

Remote mount:
  /volume1/hugcivi/remotes/pc-comfyui
    -> //PC/ComfyUI/models
  /volume1/hugcivi/remotes/win-documents
    -> //PC/Documents
  /volume1/hugcivi/remotes/studio-nas
    -> //studio-nas/Models

Docker mount:
  /volume1/hugcivi/remotes:/data_remote

HugCivi transfer target:
  PC ComfyUI Checkpoints -> kind=local_mount, remote_path=pc-comfyui/checkpoints
  PC ComfyUI LoRA        -> kind=local_mount, remote_path=pc-comfyui/loras
  Windows Documents      -> kind=local_mount, remote_path=win-documents
  Studio NAS Models      -> kind=local_mount, remote_path=studio-nas
```

주의:

- Synology remote folder mount가 Docker bind mount 안에서 안정적으로 보이는지는 실제 DSM/Container Manager/Portainer 환경에서 확인해야 한다.
- DSM의 File Station mount와 Linux kernel mount의 권한/가시성은 다를 수 있다.
- 운영 안정성이 부족하면 host-level CIFS mount 또는 rclone target이 더 낫다.

## PC 내부망 전송

Receiver 없는 내부망 전송 경로:

```text
Windows PC
  -> D:\ComfyUI\models 를 SMB 공유

Synology
  -> //PC/ComfyUI/models 를 /volume1/hugcivi/remotes/pc-comfyui 로 mount

HugCivi Docker
  -> /volume1/hugcivi/remotes:/data_remote

HugCivi UI
  -> PC ComfyUI target 선택
  -> checkpoints/loras 목적지 선택
  -> copy-only job
```

이 방식은 Receiver보다 덜 제품화되어 있지만, 내부망에서는 가장 짧다. PC가 꺼지면 mount가 끊기고 전송은 실패한다. 실패는 local `/data`를 변경하지 않아야 하며 retry하면 된다.

## 친구 공유

친구와 인터넷을 거친 공유는 `/data_remote`만으로 완성하지 않는다.

가능한 운영 형태:

1. 친구가 Synology에서 `HugCiviDrop` 같은 공유 폴더를 만든다.
2. 내 Synology가 그 폴더를 SFTP/WebDAV/CIFS/Drive 방식 중 가능한 방식으로 host에 연결한다.
3. HugCivi에는 그 host 연결 결과만 `/data_remote/friend-drop`으로 보인다.
4. HugCivi는 `friend-drop`을 copy-only 대상 또는 read-only import source로만 다룬다.

이것은 "친구 라이브러리 live browse"가 아니다. 더 세련된 다인 공유가 필요하면 별도 후속 설계가 필요하다.

후속 방향:

- Synology Drive/ShareSync mailbox.
- encrypted share capsule.
- manifest/catalog 기반 친구 라이브러리 표시.
- 선택한 파일만 `/data`로 import.

즉 `/data_remote`는 당장의 단순하고 강한 도구이고, 친구 라이브러리 UX는 그 위에 나중에 얹을 수 있는 별도 레이어다.

## 구현 순서

1. 설정과 경계
   - `DATA_REMOTE_DIR=/data_remote` env 추가.
   - Docker/compose/Portainer에 optional `/data_remote` volume 문서화.
   - `/data_remote` 존재 여부와 writable/read-only 상태를 status payload에 추가.

2. 경로 safety
   - `existing_data_remote_path()` 또는 별도 helper 추가.
   - `/data_remote` root 전송 금지.
   - target base path escape, symlink escape, unsafe segment 테스트 추가.

3. Target kind
   - `transfer_targets.kind='local_mount'` 허용.
   - target validation에 `remote_path` as `/data_remote` relative path 추가.
   - raw host/path/URL 입력 금지.

4. Tree picker
   - `GET /api/transfer/targets/{target_id}/local-mount/tree`.
   - direct child directory pagination은 Receiver tree와 같은 shape 재사용.
   - symlink folder skip.

5. Preflight
   - source `/data` safety 유지.
   - destination `/data_remote/<base>/<subpath>` safety 추가.
   - file count/byte estimate.
   - target writable probe.

6. Copy runner
   - `transfer_copy` handler에 local mount backend 추가.
   - temp file + atomic rename.
   - include pattern, skip existing, partial cleanup.
   - manifest 기록.

7. UI
   - target kind selector에 `연결 폴더` 추가.
   - settings target editor에 local mount fields 추가.
   - transfer modal에 local mount tree picker 추가.
   - Receiver tree picker와 같은 component를 재사용.

8. Docs/build
   - README, configuration, operations, architecture, feature-code-map 업데이트.
   - targeted transfer tests, full pytest.
   - local image build.

## 테스트 계획

Backend:

- `local_mount` target create/update/list/delete.
- `/data_remote` root target 거부.
- `remote_path` absolute, traversal, backslash escape 거부.
- symlink base path 또는 symlink child traversal 거부.
- 일반 source는 `/data` 내부 existing path만 허용하고 `/data` root는 거부.
- `/api/transfer/data-root/*`는 local mount target만 허용하고 browser-provided `source_path` 없이 `/data` contents clone job을 생성.
- allowed source prefix 밖 source 거부.
- local mount tree endpoint가 token 없이도 auth 뒤에서 target-relative folders만 반환.
- tree endpoint가 files와 symlink folders를 건너뜀.
- preflight가 read-only target을 실패로 표시.
- file copy가 temp file 후 final rename.
- folder copy가 include pattern을 지킴.
- existing destination은 기본 skip.
- failed copy가 partial cleanup 대상이 됨.

Frontend/template:

- target kind selector에 `연결 폴더` 존재.
- local mount target editor가 Receiver token field를 표시하지 않음.
- transfer modal이 local mount tree picker를 표시.
- payload는 `target_id`, `source_path`, `destination_subpath`만 전송.
- raw `/data_remote` absolute path가 browser payload에 들어가지 않음.

Operations:

- Docker optional mount 예시.
- 여러 `/data_remote/<name>` 연결 폴더와 target별 정책 예시.
- Synology remote folder mount 예시.
- Windows PC SMB share 예시.
- mount offline/read-only/permission denied troubleshooting.

## Open Questions

- `/data_remote`를 Docker image 기본 빈 폴더로 만들지, volume이 없으면 기능을 숨길지.
- `local_mount` target이 read-only import source도 겸할지, 첫 구현은 destination-only로 제한할지.
- 기존 folder tree component와 Receiver tree component를 얼마나 통합할지.
- local copy progress를 파일 단위로만 표시할지, byte 단위 progress도 구현할지.
- 같은 filesystem 내 reflink/hardlink/copy_file_range 최적화를 허용할지.
- Synology File Station remote mount가 Docker container에서 항상 보이는지 실제 환경별 확인이 필요하다.

## References

- Synology DSM File Station supports mounting remote CIFS shared folders through `Tools > Mount Remote Folder > CIFS Shared Folder`: https://kb.synology.com/en-global/DSM/help/FileStation/mountremotevolume?version=7
- Synology QuickConnect can help DSM/Drive/File Station services connect without manual port forwarding, but `/data_remote` does not depend on QuickConnect directly: https://global.download.synology.com/download/Document/Software/WhitePaper/Os/DSM/All/enu/Synology_QuickConnect_White_Paper_enu.pdf

## Handoff Notes

- 이 문서는 현재 구현을 설명하지 않는다. 구현 전까지 README의 사용자-facing 기능 목록에 "지원됨"으로 올리지 않는다.
- `/data_remote`는 보관함이 아니라 연결된 복사 대상 영역이다. 라이브러리 index, 삭제, rename, move 기능에 섞지 않는다.
- 첫 구현은 destination-only `local_mount` target으로 제한한다.
- Receiver와 rclone은 제거하지 않는다. `/data_remote`가 기본 권장 경로가 되더라도 각각의 장점이 있는 보조 backend로 유지한다.
