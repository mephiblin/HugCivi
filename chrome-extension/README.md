# HugCivi Remote Chrome Extension

HugCivi 웹 UI를 그대로 두고 추가로 쓰는 편의용 크롬 확장입니다. 현재 탭 URL이나 직접 입력한 값을 HugCivi 서버의 기존 `/api/jobs/bulk` API로 전송합니다.

## 설치

1. Chrome에서 `chrome://extensions`를 엽니다.
2. 오른쪽 위 `개발자 모드`를 켭니다.
3. `압축해제된 확장 프로그램을 로드`를 누릅니다.
4. 이 저장소의 `chrome-extension` 폴더를 선택합니다.

운영 서버에서는 HugCivi 웹 UI 우측 상단의 `애드온` 버튼으로 zip 파일을 받은 뒤 압축을 풀고 `hugcivi-chrome-extension` 폴더를 선택합니다.

## 설정

- 서버 주소: `http://NAS_IP:8088`처럼 HugCivi 웹 UI에 접속하는 주소를 입력합니다.
- ID/PW: HugCivi 웹 UI Basic Auth와 동일한 값을 입력합니다.
- 저장 폴더: 비우면 HugCivi의 자동 분류를 그대로 사용합니다.

단축키 기본값은 `Alt+Shift+H`입니다. Chrome의 `chrome://extensions/shortcuts`에서 바꿀 수 있습니다.

## 권한

서버 주소가 IP/포트로 바뀔 수 있어서 `host_permissions`는 `<all_urls>`로 둡니다. 확장은 입력한 HugCivi 서버로 API 요청을 보내고, 단축키 요청 때 현재 활성 탭의 URL만 읽습니다.

## 개발 메모

- 설정과 최근 요청 상태는 `chrome.storage.local`에 저장됩니다. PW도 여기에 저장되므로 개인 PC용 편의 확장으로 취급하세요.
- `shared.js`는 Basic Auth 헤더 생성, 서버 주소 정규화, `/api/jobs/bulk`, `/api/jobs` 호출을 담당합니다.
- `background.js`는 `Alt+Shift+H` 명령, 현재 탭 URL 제출, badge/notification 상태를 담당합니다.
- `popup.js`는 설정 저장, 직접 입력/현재 탭 요청, 2.5초 작업 목록 폴링과 진행도 표시를 담당합니다.
- `chrome://`, `file://`, 빈 탭처럼 HTTP/HTTPS가 아닌 현재 탭은 전송하지 않습니다.
