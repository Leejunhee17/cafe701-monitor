# 미니멀 UI 리뉴얼 및 모니터 루프 정리

**날짜:** 2026-05-20
**파이프라인:** standard-safe → deep-balanced
**변경 파일:** 2

## 수행 내용

Cafe 701 PWA를 iOS 기본 앱에 가까운 미니멀 스타일로 리뉴얼하고, 화면 잠금 상태에서도 서버 감시가 유지된다는 안심 메시지를 더 명확히 보여주도록 개선했다. 이후 서버의 `monitor_loop()`를 작은 helper 함수들로 나눠 SSE broadcast, push notification, watcher snapshot, sleep cadence 책임을 분리했다. 기존 route, JSON payload, SSE 계약, Service Worker/manifest 경로는 유지했다.

## 변경 파일

- `templates/index.html` — iOS 스타일 색상/카드/버튼/상태 배너 적용, 문구 개선, `body.is-watching` 상태 클래스 연결
- `server.py` — monitor loop 내부 로직을 `_watcher_snapshot`, `_broadcast_sse_numbers`, `_send_pending_push_notifications`, `_poll_once`, `_sleep_until_next_poll`로 분리

## 주요 결정

| 결정 | 선택 | 이유 |
| --- | --- | --- |
| 디자인 방향 | iOS-like minimalist | 사용자에게 앱 같은 안정감과 명확한 상태 인지를 제공하기 위해 |
| 핵심 UX | 잠금화면 알림 안심감 강조 | 사용자가 화면을 꺼도 되는지 확신하는 것이 앱의 핵심 가치이기 때문 |
| 리팩터 방식 | in-place helper extraction | 작동 중인 Web Push/SSE 계약을 깨지 않고 가독성을 개선하기 위해 |
| 커밋 단위 | UI 리뉴얼 / 서버 루프 정리 분리 | 배포·롤백 시 변경 원인을 명확히 하기 위해 |

## 남은 위험

- 실제 iPhone PWA visual QA는 필요함 — Safari/Home Screen에서 여백, safe-area, 알림 권한 상태 확인 권장
- `uv.lock`이 여전히 untracked 상태 — lockfile 관리 여부를 별도 결정 필요

## 다음 단계

- [ ] Render 배포 후 iPhone 홈 화면 PWA에서 감시 시작/잠금화면 알림/발견 상태 확인
- [ ] 필요하면 `server.py`를 별도 모듈(`push_state.py`, `ocr.py`)로 추가 분리 검토
