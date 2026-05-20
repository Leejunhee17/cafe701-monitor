# iOS PWA Web Push 서버 감시 구현

**날짜:** 2026-05-20
**파이프라인:** deep-balanced
**변경 파일:** 9

## 수행 내용

아이폰 홈 화면 PWA가 백그라운드/잠금 상태에서도 알림을 받을 수 있도록 감시 책임을 브라우저 SSE 연결에서 서버-side watch로 옮겼다. 서버가 Push Subscription과 감시 번호를 저장하고, OCR 감시 루프에서 번호 발견 시 `pywebpush`로 Web Push를 발송한다. 기존 SSE 흐름은 앱이 열려 있을 때의 실시간 UI 갱신 및 fallback으로 유지했다.

## 변경 파일

- `server.py` — Web Push 설정/상태 저장/API, SSE와 push watch 분리, monitor loop push 발송, service worker/manifest root route 추가
- `templates/index.html` — Service Worker/PushManager 등록, 서버 watch 생성/삭제, fallback UX, 잠금화면 푸시 상태 UI 및 입력 검증 추가
- `static/service-worker.js` — `push`/`notificationclick` 처리 및 알림 표시 추가
- `static/manifest.webmanifest` — 홈 화면 설치용 PWA manifest 추가
- `requirements.txt` — `pywebpush` 의존성 추가
- `pyproject.toml` — `pywebpush` 의존성 추가
- `render.yaml` — VAPID/PUSH_STATE_FILE 환경 변수 placeholder 추가
- `README.md` — iOS PWA Web Push 요구사항, VAPID 키, Render 설정 문서화
- `.gitignore` — 로컬 push state와 `.pi-lens/` 제외 추가

## 주요 결정

| 결정                 | 선택                                                           | 이유                                                                       |
| -------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 백그라운드 감시 위치 | 서버-side watch                                                | iOS PWA는 백그라운드 polling/SSE 지속을 보장하지 않기 때문                 |
| 알림 전송 방식       | Web Push + Service Worker `showNotification()`                 | iOS 홈 화면 PWA의 잠금화면 알림 경로에 맞춤                                |
| 상태 저장            | 단일 프로세스 JSON 파일                                        | 현재 Render 단일 worker 앱에 맞는 최소 구현, 외부 DB 없이 빠르게 적용 가능 |
| SSE 역할             | foreground UI/fallback 유지                                    | 앱이 열려 있을 때 현재 번호 표시와 소리/진동 UX를 보존                     |
| 중복 알림 방지       | 서버 push 성공 세션에서만 foreground `new Notification()` 억제 | stale localStorage가 모든 알림을 막는 문제를 피하면서 중복 가능성을 줄임   |

## 남은 위험

- Render `/tmp` 기반 `PUSH_STATE_FILE`은 재시작/재배포 시 사라질 수 있음 — 장기적으로 persistent disk 또는 외부 저장소 권장
- iOS Web Push는 iOS/iPadOS 16.4+, HTTPS, 홈 화면 설치, 알림 권한, 안정적인 VAPID 키가 필요함 — 실제 기기 smoke test 필요
- Web Push는 exactly-once 전달을 보장하지 않음 — 희귀한 중복/누락 가능성을 사용자 안내 또는 재시도 정책으로 보완 가능
- `uv.lock`은 현재 git에서 untracked/stale 상태로 보임 — 프로젝트에서 lockfile을 관리하려면 별도 재생성 필요

## 다음 단계

- [ ] `python -m pywebpush --gen-vapid`로 VAPID 키 생성 후 Render env에 설정
- [ ] iPhone 홈 화면 PWA에서 알림 권한 허용 후 잠금화면 push smoke test
- [ ] Render 재시작 후 watch 유지가 필요하면 persistent disk 또는 외부 DB 도입 검토
