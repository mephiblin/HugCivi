# 전송 기능 설계

작성일: 2026-07-02

상태: 설계 문서. 이 문서의 전송 기능은 아직 구현되지 않았다.

## 목표

HugCivi의 `/data` archive에서 선택한 파일이나 폴더를 친구, 개인 원격 드라이브, 외부 백업 위치로 안전하게 전송한다.

전송은 다운로드와 같은 장기 작업으로 취급한다. HTTP 요청 안에서 직접 복사하지 않고, DB-backed job으로 큐에 넣어 진행률, 로그, 재시도, 취소, 속도 제한을 관리한다.

## 기본 방향

초기 구현은 `rclone copy` 기반 outbound 전송만 지원한다.

- HugCivi/NAS는 외부에 새 포트를 열지 않는다.
- 전송 대상은 관리자가 미리 등록한 rclone remote/profile만 사용한다.
- 사용자는 보관함 또는 폴더 화면에서 선택 항목을 전송 큐에 넣는다.
- 기본 동작은 복사이며, 원격 파일 삭제가 가능한 `sync`, `move`, delete 옵션은 초기 UI에서 제공하지 않는다.

rclone은 SFTP, WebDAV, S3-compatible storage, Google Drive, Dropbox 등 여러 remote backend를 이미 지원하므로 HugCivi가 각 provider 클라이언트를 직접 구현하지 않는다.

참고:

- rclone documentation: <https://rclone.org/docs/>
- rclone global flags: <https://rclone.org/flags/>
- rclone SFTP backend: <https://rclone.org/sftp/>
- rclone WebDAV backend: <https://rclone.org/webdav/>
- rclone crypt backend: <https://rclone.org/crypt/>
- rclone serve: <https://rclone.org/commands/rclone_serve/>
- rclone config encryption: <https://rclone.org/docs/#configuration-encryption>

## 다른 IP 전송

rclone은 원격 IP나 도메인에 있는 storage endpoint로 전송할 수 있다. 예시는 다음과 같다.

- 친구의 SFTP 서버
- 친구 또는 내 Synology WebDAV
- VPS의 SFTP/S3-compatible endpoint
- 클라우드 드라이브 remote
- VPN 안쪽의 개인 NAS

보안 관점에서 중요한 구분:

| 방식 | 설명 | 권장도 |
| --- | --- | --- |
| Outbound copy | HugCivi가 외부 SFTP/WebDAV/S3 remote로 접속해서 보냄 | 기본 권장 |
| VPN-only receive | Tailscale/WireGuard 내부에서만 HugCivi 또는 rclone serve를 노출 | 제한적 허용 |
| Internet open receive | NAS/HugCivi/rclone serve 포트를 인터넷에 직접 개방 | 기본 비권장 |

초기 기능은 outbound copy만 구현한다.

## 위협 모델

### Credential 유출

rclone remote에는 토큰, 비밀번호, app password, SSH key 경로가 들어갈 수 있다. 따라서 `/config/rclone/rclone.conf`와 HugCivi DB 백업은 credential backup으로 취급한다.

정책:

- rclone config는 `/config/rclone/rclone.conf` 같은 파일에 보관한다.
- DB에는 remote 이름, 표시 이름, 정책, 기본 경로만 저장한다.
- 토큰/비밀번호 원문을 HugCivi DB에 중복 저장하지 않는다.
- 가능한 경우 rclone config encryption을 지원한다.
- `/config` 백업 문서에는 rclone config 포함 시 원격 credential도 함께 백업된다고 명시한다.

### 중간자 공격

SFTP나 WebDAV 접속에서 서버 검증이 약하면 공격자가 원격지를 가로챌 수 있다.

정책:

- SFTP는 `known_hosts` 기반 host key 검증을 요구한다.
- host key가 누락되거나 바뀌면 자동으로 신뢰하지 않는다.
- WebDAV/S3는 HTTPS를 기본으로 요구한다.
- 자체 인증서나 사설 CA는 명시적으로 등록한 경우에만 허용한다.
- `--no-check-certificate`류 옵션은 기본 금지한다.

### 원격지 불신

친구 드라이브나 외부 클라우드는 저장소 운영자를 완전히 신뢰하지 못할 수 있다.

정책:

- 불신 원격에는 rclone `crypt` remote 사용을 권장한다.
- crypt remote를 사용하면 업로드 전 로컬에서 암호화되고, 원격 저장소에는 암호화된 파일이 남는다.
- 파일명 암호화 여부는 remote 특성에 따라 선택하되, 친구 공유용이면 path/name 암호화를 기본 권장한다.

### 내부망 악용

UI에서 임의 IP, URL, remote path를 아무나 입력할 수 있으면 HugCivi가 내부망 스캐너나 데이터 유출 도구처럼 악용될 수 있다.

정책:

- 일반 사용자는 임의 host/IP를 입력하지 못한다.
- 전송 target 생성은 관리자만 가능하다.
- 전송 job은 등록된 target id만 참조한다.
- target에는 허용 remote prefix를 저장하고, job 생성 시 그 prefix 밖으로 나가지 못하게 한다.
- HugCivi UI 인증이 꺼진 상태에서는 전송 기능을 비활성화하거나 관리자 설정 화면을 숨긴다.

### 삭제 사고

`rclone sync`는 destination을 source와 같게 맞추기 위해 원격 파일 삭제가 발생할 수 있다. 친구 드라이브나 백업 드라이브에서 치명적인 사고가 될 수 있다.

정책:

- 초기 구현은 `copy`만 허용한다.
- `sync`, `move`, `delete-before`, `delete-during`, `delete-excluded`는 기본 금지한다.
- 나중에 sync를 추가할 경우 별도 target capability, dry-run, 2단계 확인, 삭제 목록 preview가 필요하다.

## 제안 DB 모델

현재 HugCivi는 `jobs.job_kind`와 internal job runner가 있으므로, 전송도 internal job으로 처리한다.

### transfer_targets

전송 대상 profile.

```text
id INTEGER PRIMARY KEY
name TEXT NOT NULL
remote_name TEXT NOT NULL
remote_path TEXT NOT NULL DEFAULT ''
mode TEXT NOT NULL DEFAULT 'copy'
enabled INTEGER NOT NULL DEFAULT 1
policy_json TEXT NOT NULL DEFAULT '{}'
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

`policy_json` 예:

```json
{
  "bwlimit": "8M",
  "transfers": 1,
  "checkers": 2,
  "require_check": false,
  "allow_overwrite": true,
  "allowed_source_prefixes": ["civitai", "huggingface", "gallery-dl"],
  "schedule_window": "02:00-07:00"
}
```

### jobs

전송 실행은 기존 `jobs` table을 사용한다.

```text
job_kind = 'transfer_copy'
source = 'transfer'
status = queued/running/paused/done/failed/canceled
parsed_json = transfer request/options
target_dir = local source path 또는 source folder
artifact_path = optional manifest path
```

`parsed_json` 예:

```json
{
  "target_id": 1,
  "source_path": "civitai/images/example",
  "destination_subpath": "shared/example",
  "mode": "copy",
  "verify": "size-modtime",
  "dry_run": false
}
```

### job_content_refs

전송 source와 manifest를 기록한다.

```text
role = source | manifest | verification
path = relative /data path or /config artifact path
```

### job_artifacts

전송 manifest, dry-run 결과, 검증 결과를 저장한다.

```text
kind = transfer_manifest | transfer_dry_run | transfer_check
path = /config/... artifact path
url = optional UI download URL
```

## rclone 실행 모델

초기 wrapper는 subprocess 기반으로 충분하다.

기본 명령 형태:

```text
rclone copy /data/<source> <remote_name>:<remote_path>/<destination_subpath>
  --config /config/rclone/rclone.conf
  --transfers <policy.transfers>
  --checkers <policy.checkers>
  --bwlimit <policy.bwlimit>
  --stats 1s
  --stats-one-line
```

운영 원칙:

- shell string 대신 argv list로 실행한다.
- remote name과 path는 등록 target에서만 생성한다.
- 사용자 입력 path는 `/data` 상대 경로로 정규화하고 symlink escape를 막는다.
- rclone stdout/stderr를 job log에 축약 저장한다.
- 긴 로그는 `/config/transfer-logs` 같은 artifact로 분리할 수 있다.
- pause는 프로세스 terminate 후 `paused` 처리하고, resume 시 같은 copy 명령을 다시 실행한다.

rclone은 같은 copy를 다시 실행하면 이미 존재하는 파일을 비교하고 필요한 파일만 전송할 수 있다. 따라서 pause/resume은 세밀한 byte-level resume보다 재실행 기반으로 시작한다.

## 검증 정책

초기 기본 검증은 rclone의 기본 비교(size/modtime 또는 backend checksum)에 맡긴다.

선택 옵션:

- `--checksum`: backend가 checksum을 제공하는 경우 더 강한 비교.
- `rclone check`: 전송 후 별도 검증 job 또는 같은 job의 후처리.
- crypt remote 사용 시 checksum 제약이 있을 수 있으므로, 검증 방식은 target별 capability로 관리한다.

UI 표현:

- `전송 완료`: copy command 성공.
- `검증 완료`: check까지 성공.
- `검증 생략`: target policy가 검증을 요구하지 않음.
- `검증 불가`: remote/backend 특성상 checksum 비교가 제한됨.

## 큐와 NAS 보호

전송은 다운로드, ZIP, transcode와 자원 성격이 다르다.

권장 기본값:

```text
TRANSFER_MAX_CONCURRENT=1
TRANSFER_DEFAULT_TRANSFERS=1
TRANSFER_DEFAULT_CHECKERS=2
TRANSFER_DEFAULT_BWLIMIT=8M
TRANSFER_LOG_MAX_BYTES=1048576
```

운영 정책:

- NAS 기본값은 동시 전송 1개.
- 전송 target별 bandwidth limit을 둔다.
- 야간 스케줄 window를 지원한다.
- 다운로드 job과 동시에 과도한 network/disk I/O가 발생하지 않도록 global pressure check를 둔다.
- ZIP/transcode 같은 internal job과 같은 runner를 쓸 수 있지만, 장기적으로는 transfer 전용 concurrency 설정을 둔다.

## UI 설계

### Target 설정

설정 화면에 `전송 대상` pane을 추가한다.

필드:

- 표시 이름
- rclone remote 이름
- remote base path
- bandwidth limit
- transfers/checkers
- 검증 여부
- 암호화 remote 여부 표시
- 활성/비활성

초기 버전은 rclone remote 생성 UI를 제공하지 않고, `/config/rclone/rclone.conf`에 이미 설정된 remote를 선택하게 한다.

### 보관함/폴더 액션

보관함 카드 또는 폴더 context menu:

- `전송`
- target 선택
- destination subpath 확인
- 예상 항목 수/크기 표시
- 큐 등록

모바일:

- 카드 액션 메뉴 안에 `전송` 추가.
- 큰 설정 폼은 modal보다 전체 화면 sheet가 낫다.

### 작업 목록

기존 작업 목록에 전송 job을 표시한다.

표시 값:

- 상태
- 대상 target
- 전송량
- 속도
- 남은 시간
- 마지막 로그
- 취소/일시정지/재개
- manifest/check 결과

## 단계별 구현

1. 문서와 설정 위치 확정
   - `/config/rclone/rclone.conf`
   - `/config/rclone/known_hosts`
   - `/config/transfer-logs`

2. DB migration
   - `transfer_targets`
   - 필요한 index
   - settings/env 기본값

3. Backend target API
   - list/create/update/delete
   - remote name/path validation
   - auth required

4. Transfer internal job handler
   - `transfer_copy`
   - rclone availability check
   - subprocess wrapper
   - progress parsing/logging
   - cancel/pause handling

5. UI
   - settings target pane
   - 보관함/폴더 transfer action
   - 작업 목록 표시

6. 검증과 운영 안전장치
   - dry-run
   - path escape tests
   - disallowed flag tests
   - credential redaction tests
   - bandwidth/concurrency env tests

7. 고급 기능
   - scheduled recurring transfer
   - post-copy `rclone check`
   - crypt remote helper
   - transfer manifest export
   - friend share preset

## 비권장 초기 범위

초기 구현에 넣지 않는다.

- HugCivi 자체를 인터넷 파일 서버로 공개
- rclone `serve sftp/webdav` 자동 실행
- arbitrary user-entered remote URL/IP
- `sync`/`move`/remote delete
- provider별 OAuth setup wizard
- 여러 remote 간 server-side transfer

이 항목들은 기능은 가능하지만 공격면과 운영 복잡도가 커진다.

## 결론

HugCivi의 전송 기능은 "공유 서버"보다 "안전한 outbound 전송 큐"로 설계해야 한다.

rclone을 엔진으로 쓰되 HugCivi는 다음을 책임진다.

- 누가 어떤 target으로 보낼 수 있는지 제한
- 어떤 local path를 보낼 수 있는지 제한
- NAS 자원을 보호하는 큐/속도 정책
- credential과 로그 redaction
- 전송 결과와 검증 결과 기록

이 방향이면 친구나 원격 드라이브로 전송하는 자동화는 가능하면서도, NAS에 새 외부 진입점을 만들지 않는다.
