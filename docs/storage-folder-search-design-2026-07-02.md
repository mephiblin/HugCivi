# 저장 폴더 검색창 설계 2026-07-02

상태: 구현됨. `1cd733d`에서 하단 `새 폴더` 입력 영역을 폴더 검색창으로 바꾸고, 새 폴더 생성과 이동 대상 선택을 폴더 트리 기반 팝업으로 처리하도록 반영했다. `c4cefa6`의 구독 메인 작업 목록 UI와 함께 다시 확인했으며, 현재 코드 기준으로 두 UI는 충돌하지 않는다.

이 문서는 왼쪽 `저장 폴더` 패널 하단의 기존 `새 폴더` 입력 영역을 폴더 검색창으로 바꾼 구현 기준 기록이다. 사용자가 캡처에서 빨간색으로 표시한 영역은 구현 전에는 `new-folder-form`이었고, `/folders` POST로 새 폴더를 생성했다.

## 목표

- `저장 폴더` 트리 아래쪽 입력 영역을 빠른 폴더 검색창으로 사용한다.
- 긴 `/data` 폴더 트리에서 원하는 경로를 직접 스크롤하지 않고 찾게 한다.
- 검색 결과 선택 시 기존 폴더 선택 동작과 동일하게 `target_folder`, `selected-folder`, 라이브러리 경로를 갱신한다.
- 기존 새 폴더 생성 기능은 제거하지 않고, Windows 파일 탐색기처럼 폴더 트리 우클릭 메뉴의 `새 폴더` 액션으로 옮긴다.
- 우클릭 메뉴의 `이동`은 텍스트 입력 prompt가 아니라 폴더 트리 선택 팝업으로 처리한다.
- 데스크톱과 모바일 모두에서 입력창, 결과, 버튼 텍스트가 겹치지 않게 유지한다.

## 비목표

- 파일 전체 검색이나 라이브러리 카드 검색을 이 단계에 포함하지 않는다.
- `/data` 밖의 경로를 검색하거나 만들 수 있게 하지 않는다.
- 새 폴더 생성 API인 `POST /folders`를 삭제하지 않는다.
- 검색어 입력만으로 폴더를 자동 생성하지 않는다.
- 다운로드 작업 목록, 구독 목록, 미디어 뷰어의 검색 기능까지 함께 바꾸지 않는다.

## 구현 결과

구현된 동작:

- 하단 `new-folder-form`은 `folder-search-form`으로 대체되었다.
- 검색은 현재 로드된 `.folder-picker[data-path]` 항목을 대상으로 한다.
- 검색 결과 선택, Enter 선택, Escape 초기화를 지원한다.
- 폴더 트리 우클릭 메뉴에는 `새 폴더`가 추가되었다.
- `새 폴더` 팝업은 부모 경로를 보여주고 폴더 이름만 입력받는다.
- 새 폴더 생성은 `POST /api/folders` JSON API를 사용하며, 성공 시 폴더 트리를 갱신하고 새 폴더를 선택한다.
- 우클릭 `이동`은 기존 텍스트 prompt 대신 `이동 대상 선택` 폴더 트리 팝업을 연다.
- 이동 자체는 기존 `POST /api/fs/move` 안전 검증을 그대로 사용한다.

## 이전 상태

구현 전 UI 구조:

```html
<section class="folder-section" aria-label="저장 폴더">
  ...
  <div class="folder-tree">...</div>
</section>

<form class="new-folder-form" method="post" action="/folders">
  <label for="folder_path">새 폴더</label>
  <input id="folder_path" name="folder_path" placeholder="stable-diffusion/checkpoints" required>
  <button type="submit">폴더 생성</button>
</form>
```

관련 코드:

- `app/templates/index.html`
  - `folder_node(node)` macro
  - `.folder-picker` click handler
  - `.folder-toggle` expand/collapse handler
  - `showContextMenu(x, y, target)`
  - `handleContextAction(action)`
  - `#folder_path` input and `.new-folder-form`
- `app/static/style.css`
  - `.folder-tree`
  - `.folder-picker`
  - `.new-folder-form`
  - `.context-menu`
- `app/main.py`
  - `POST /folders`
  - `GET /api/folders`
  - `POST /api/fs/move`
  - `build_folder_tree(root, max_depth=4, max_entries=300)`

현재 `build_folder_tree()`는 깊이와 개수를 제한한다. 따라서 구현된 클라이언트 필터는 "현재 로드된 폴더 트리 안에서 검색"이라는 한계가 있다.

구현 전 우클릭 메뉴의 `이동`은 다음처럼 텍스트 prompt를 사용했다.

```text
window.prompt('이동할 대상 폴더를 /data 기준 경로로 입력하세요.', '')
```

이 방식은 경로 오타, 목적지 착각, 모바일 입력 불편이 생기기 쉬우므로 폴더 트리 선택 팝업으로 바꾼다.

## 구현 UI

하단 영역을 검색 중심으로 바꾼다.

```text
폴더 검색
[ stable-diffusion/checkpoints ]

3개 결과
[폴더] stable-diffusion/checkpoints
[폴더] stable-diffusion/checkpoints/illustrious
[폴더] stable-diffusion/checkpoints/sd-1.5
```

현재 배치:

- 라벨은 `폴더 검색`.
- 입력 placeholder는 현재 예시와 같은 `stable-diffusion/checkpoints`를 유지한다.
- 하단 검색창에는 `폴더 생성` 버튼을 두지 않는다.
- 폴더 생성은 폴더 트리에서 마우스 우클릭 후 `새 폴더` 메뉴를 누르는 방식으로 옮긴다.
- 검색어가 없을 때는 결과 목록을 숨기고 기존 폴더 트리만 보여준다.
- 검색어가 있을 때는 일치하는 폴더를 트리에서 강조하거나, 하단 입력 바로 위에 작은 결과 목록을 보여준다.

현재 구현은 하단 검색 결과 목록을 사용한다. 검색 결과 클릭은 `selectFolderPath(path)`를 호출해 기존 `.folder-picker` 선택 동작과 같은 상태 갱신을 공유한다.

## 상호작용

검색 입력:

- 사용자가 입력하면 150ms 정도 debounce 후 검색한다.
- 대소문자는 구분하지 않는다.
- `/`, 공백, `-`, `_`는 그대로 허용한다.
- 앞뒤 공백은 검색 시 제거한다.
- `Escape`를 누르면 검색어와 결과를 지운다.
- `Enter`를 누르면 첫 번째 결과 또는 완전 일치 결과를 선택한다.

결과 선택:

- 결과를 클릭하면 기존 폴더 선택과 동일하게 동작한다.
- `target_folder.value`를 선택한 상대 경로로 설정한다.
- `selected-folder`를 `/data/<path>`로 표시한다.
- 기존 `.folder-picker.selected` 상태도 가능하면 같은 경로에 맞춰 갱신한다.
- `switchToLibrary(path)`를 호출해 라이브러리 패널을 해당 폴더로 전환한다.

트리 표시:

- 검색 결과에 해당하는 노드는 트리에서도 강조한다.
- 트리 안에 이미 존재하는 결과라면 부모 노드를 자동 확장한다.
- 검색어를 지우면 사용자가 직접 펼쳐 둔 폴더 상태를 가능한 한 유지한다.

새 폴더 생성:

- 폴더 트리의 폴더 행에서 우클릭하면 context menu에 `새 폴더` 항목을 표시한다.
- `/data` 루트 또는 특정 폴더를 우클릭한 위치가 새 폴더의 부모 경로가 된다.
- `새 폴더`를 누르면 팝업을 열고 전체 경로가 아니라 폴더 이름만 입력하게 한다.
- 팝업에는 부모 경로를 읽기 전용으로 보여준다. 예: `위치: /data/stable-diffusion/checkpoints`.
- 확인 시 `POST /api/folders`를 사용해 `parent_path`와 `folder_name`을 JSON으로 보낸다. 기존 `POST /folders`는 호환용으로 유지한다.
- 성공 후 `/api/folders`로 트리를 새로고침하고 새 폴더를 선택한다.
- 취소 시 아무 파일시스템 변경도 하지 않는다.

우클릭 메뉴:

- 기본 항목은 `새 폴더`, `미리보기`, `다운로드`, `속성`, `이름 변경`, `이동`, `삭제` 순서를 권장한다.
- `새 폴더`는 폴더 대상에서만 활성화한다. 파일 카드 우클릭에는 표시하지 않거나 비활성화한다.
- `/data` 루트에서는 `새 폴더`만 허용하고, 기존 정책대로 루트의 이름 변경, 이동, 삭제는 비활성화한다.
- 생성 팝업의 기본 이름은 `새 폴더`로 둘 수 있지만, 같은 이름이 있을 경우 서버 오류를 보여주거나 `새 폴더 2` 같은 후보를 제안한다.

이동 팝업:

- 우클릭 메뉴의 `이동`을 누르면 텍스트 prompt 대신 `이동 대상 선택` 팝업을 연다.
- 팝업 안에는 `/data` 폴더 트리를 표시한다.
- 사용자가 목적지 폴더를 선택하면 하단에 선택 경로를 보여준다. 예: `대상: /data/gallery-dl`.
- 팝업 하단에는 `확인`, `취소` 버튼을 둔다.
- `확인`은 대상 폴더를 선택하기 전까지 비활성화한다.
- 확인 시 기존 `POST /api/fs/move`에 `{path, destination}`을 보낸다.
- 취소 또는 바깥 클릭은 이동을 실행하지 않는다.
- 이동하려는 폴더 자신, 자신의 하위 폴더, symlink escape 가능성이 있는 위치는 선택 불가 상태로 표시한다. 서버의 `api_move_path()` 검증은 그대로 최종 방어선으로 유지한다.
- 이동 성공 후 폴더 트리, 라이브러리, 선택 폴더 표시를 갱신한다.

## 검색 범위

### 1차 구현: 로드된 폴더 트리 검색

장점:

- 백엔드 변경 없이 시작할 수 있다.
- 현재 Jinja로 렌더링된 `folder_tree`와 `.folder-picker[data-path]`만으로 구현 가능하다.
- 빠르고 위험도가 낮다.

제한:

- `build_folder_tree(max_depth=4, max_entries=300)` 밖에 있는 깊은 폴더는 검색되지 않는다.
- 많은 폴더를 가진 `/data`에서는 사용자가 기대하는 "전체 검색"과 다를 수 있다.

UI 문구:

```text
현재 표시된 폴더에서 검색
```

또는 결과가 없을 때:

```text
표시된 폴더에서 결과가 없습니다.
```

### 2차 구현: 서버 폴더 검색 API

깊은 경로까지 찾으려면 별도 API를 추가한다.

권장 route:

```text
GET /api/folders/search?q=<query>&limit=50
```

응답 예:

```json
{
  "ok": true,
  "query": "checkpoints",
  "items": [
    {
      "name": "checkpoints",
      "path": "stable-diffusion/checkpoints",
      "depth": 2,
      "has_children": true
    }
  ],
  "truncated": false
}
```

백엔드 원칙:

- 검색 시작점은 항상 `DATA_ROOT`다.
- 사용자 입력을 절대 파일 경로로 직접 해석하지 않는다.
- 결과 `path`는 `/data` 상대 경로만 반환한다.
- symlink는 기존 파일시스템 안전 정책과 맞춰 escape를 허용하지 않는다.
- `limit`, scan timeout, max visited directories를 둔다.
- 숨김 폴더나 시스템 폴더 제외가 필요하면 명시 정책으로 둔다.

## 구현 메모

현재 프론트엔드 함수:

```text
selectFolderPath(path)
collectVisibleFolderRows()
filterFolderRows(query)
renderFolderSearchResults(items)
clearFolderSearch()
openCreateFolderModal(parentPath)
submitCreateFolder(parentPath, folderName)
openMoveDestinationModal(sourcePath)
submitMoveToDestination(sourcePath, destinationPath)
```

기존 `.folder-picker` click handler 안의 로직을 `selectFolderPath(path)`로 뽑으면 검색 결과 클릭과 트리 클릭이 같은 동작을 공유할 수 있다.

현재 DOM:

```html
<form class="folder-search-form" role="search">
  <div class="folder-search-heading">
    <label for="folder_search">폴더 검색</label>
  </div>
  <input id="folder_search" autocomplete="off" placeholder="stable-diffusion/checkpoints">
  <div class="folder-search-status" id="folder-search-status" role="status" aria-live="polite"></div>
  <div class="folder-search-results" id="folder-search-results"></div>
</form>
```

기존 `#folder_path` id는 검색 입력에서 사용하지 않는다. 검색 입력은 `folder_search`를 사용하고, 폴더 생성 모달은 `folder_create_name`으로 이름 입력 의미를 분리한다.

폴더 생성 모달:

```html
<div class="modal" id="folder-create-modal" hidden>
  <div class="modal-dialog">
    <h2>새 폴더</h2>
    <p id="folder-create-parent">위치: /data</p>
    <label for="folder_create_name">폴더 이름</label>
    <input id="folder_create_name" autocomplete="off">
    <button type="button" id="folder-create-confirm">확인</button>
    <button type="button" id="folder-create-cancel">취소</button>
  </div>
</div>
```

이동 대상 선택 모달:

```html
<div class="modal" id="folder-move-modal" hidden>
  <div class="modal-dialog">
    <h2>이동 대상 선택</h2>
    <p id="folder-move-source">대상: /data/example</p>
    <div class="folder-picker-tree" id="folder-move-tree"></div>
    <p id="folder-move-destination">선택된 폴더 없음</p>
    <button type="button" id="folder-move-confirm" disabled>확인</button>
    <button type="button" id="folder-move-cancel">취소</button>
  </div>
</div>
```

## 모바일

- 하단 검색 영역은 터치 타깃을 최소 42px 이상으로 유지한다.
- 결과 목록이 길어질 경우 폴더 트리와 페이지 전체를 동시에 밀어내지 않도록 `max-height`와 내부 스크롤을 둔다.
- 키보드가 올라온 상태에서도 검색 입력과 첫 결과가 보이게 한다.
- 터치 환경에서는 우클릭 메뉴 대신 길게 누르기 또는 기존 카드 context menu 진입 동작과 같은 방식으로 `새 폴더`에 접근할 수 있게 한다.
- 이동 대상 선택 모달의 트리는 모바일에서 최소 280px 높이와 내부 스크롤을 둔다.

## 테스트

구현 검증:

- `tests/test_review_fixes.py`
  - 홈 템플릿에 `folder_search`, 검색 결과 영역, `folder-create-modal`, `folder-move-modal`이 선언되는지 확인한다.
  - context menu에 `새 폴더` 액션이 선언되는지 확인한다.
- 2026-07-02 재검토
  - `python3 -m py_compile app/main.py app/db.py app/subscriptions.py`: 통과.
  - Jinja JSON 상수를 대체한 인라인 `app/templates/index.html` 스크립트 `node --check`: 통과.
  - `tests/test_review_fixes.py::test_home_template_declares_storage_folder_search_ui`, `tests/test_review_fixes.py::test_api_create_folder_creates_child_and_rejects_nested_name`, `tests/test_review_fixes.py::test_home_template_declares_subscription_sidebar_ui`: `3 passed`.
  - `python3 -m pytest -q -p no:cacheprovider tests/test_review_fixes.py`: `41 passed`.
  - `python3 -m pytest -q -p no:cacheprovider`: `148 passed`.
  - `git diff --check`: 통과.
  - `app/templates/index.html`, `app/static/style.css`, `tests/test_review_fixes.py` 기준 이전 이동 prompt 문자열 없음.
- 브라우저 수동 확인
  - 검색어 입력 시 결과가 나온다.
  - Enter가 첫 결과를 선택한다.
  - Escape가 검색을 지운다.
  - 결과 선택 후 다운로드 대상 폴더와 라이브러리 경로가 바뀐다.
  - 폴더 트리 우클릭 메뉴에서 `새 폴더`를 누르면 부모 경로와 이름 입력 팝업이 열린다.
  - 새 폴더 생성 성공 후 트리가 갱신되고 새 폴더가 선택된다.
  - 우클릭 메뉴의 `이동`은 텍스트 prompt 없이 트리 선택 팝업을 연다.
  - 이동 팝업에서 확인/취소가 기대대로 동작한다.
  - 모바일 폭에서 입력창, 결과, 생성/이동 팝업이 겹치지 않는다.
- 서버 검색 API를 추가하는 경우
  - `/api/folders/search`가 `/data` 밖 경로를 반환하지 않는다.
  - 깊은 폴더 검색이 limit와 timeout 안에서 멈춘다.
  - symlink escape가 결과에 포함되지 않는다.

## 단계별 작업

1. 기존 폴더 선택 로직을 `selectFolderPath(path)`로 분리한다.
2. `new-folder-form` 하단 영역을 `folder-search-form`으로 바꾼다.
3. 로드된 `.folder-picker[data-path]` 목록을 대상으로 클라이언트 검색을 구현한다.
4. 검색 결과 클릭, Enter, Escape 동작을 연결한다.
5. context menu에 `새 폴더` 액션을 추가하고 폴더 생성 모달을 연결한다.
6. 기존 `이동`의 `window.prompt()`를 제거하고 이동 대상 트리 선택 모달을 연결한다.
7. 생성/이동 성공 후 `/api/folders` 새로고침과 선택 상태 갱신을 정리한다.
8. 데스크톱과 모바일 CSS를 정리한다.
9. 실제 사용에서 깊은 폴더 검색 필요가 확인되면 `/api/folders/search`를 추가한다.

## 위험과 결정

- 결정: 첫 UI 전환은 클라이언트 검색으로 시작해도 된다. 단, 문구나 결과 상태에서 "전체 `/data` 검색"처럼 보이게 만들지 않는다.
- 결정: 새 폴더 생성 기능은 하단 검색창이나 `+` 버튼이 아니라 폴더 트리 우클릭 메뉴의 `새 폴더`로 옮긴다.
- 결정: `이동`은 텍스트 경로 입력이 아니라 폴더 트리 선택 모달에서 확인/취소하는 방식으로 바꾼다.
- 위험: 검색 입력이 기존 폴더 생성 입력처럼 보이면 사용자가 Enter로 폴더가 만들어진다고 오해할 수 있다. 검색과 생성의 라벨, 버튼, submit 동작을 분리해야 한다.
- 위험: 트리 DOM을 직접 숨기고 펼치는 방식은 기존 사용자의 확장 상태를 잃기 쉽다. 결과 목록 방식이 초기 구현에 더 안전하다.
- 위험: 이동 대상 선택 트리가 원래 사이드바 트리와 상태를 따로 가지면 선택 표시가 엇갈릴 수 있다. 목적지 선택 모달은 `destinationPath` 상태를 별도로 갖고, 일반 `target_folder` 선택 상태와 섞지 않는다.
