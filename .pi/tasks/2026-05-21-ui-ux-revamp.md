# UI/UX 전면 개편

**날짜:** 2026-05-21
**파이프라인:** deep-balanced → standard-safe 축소
**변경 파일:** 2

## 수행 내용

Cafe 701 번호 알림 앱을 iPhone PWA 중심의 모던 카페 스타일로 전면 개편했습니다. 기존 Web Push, SSE, `/api/*` 호출 흐름은 유지하면서 현재 번호 보드, 주문번호 입력 CTA, 푸시 안내, 감시 상태, 운영시간/확인 주기 정보를 더 명확한 카드형 UI로 재구성했습니다. 서버는 템플릿에 운영시간과 폴링 주기 설정을 주입해 프론트 안내 문구가 환경 변수와 일치하도록 했습니다.

## 변경 파일

- `templates/index.html` — 모던 카페 테마 UI, iPhone safe-area/터치 타깃, 접근성, 상태 문구, Jinja 설정 주입, 영업시간 계산 보강.
- `server.py` — `index()` 렌더링 시 `open_hour`, `close_hour`, `poll_interval`, `timezone` 템플릿 컨텍스트 전달.

## 주요 결정

| 결정          | 선택                               | 이유                                                                             |
| ------------- | ---------------------------------- | -------------------------------------------------------------------------------- |
| 디자인 방향   | 모던 카페                          | 사용자가 선택한 따뜻한 카페 톤과 PWA 홈 화면 경험에 적합함                       |
| 우선 기기     | iPhone PWA                         | 잠금화면 알림·홈 화면 앱 사용 흐름이 핵심이므로 모바일 터치/세이프 영역을 우선함 |
| API 계약      | 유지                               | Push/SSE/OCR 동작 안정성을 보존하고 UI 변경 리스크를 낮춤                        |
| 서버 UX       | 템플릿 설정 주입만 적용            | 운영시간/주기 안내를 서버 설정과 맞추되 API 응답 shape는 바꾸지 않음             |
| 영업시간 계산 | `formatToParts()` + UTC+9 fallback | 한국어 locale의 `9시` 문자열이 `NaN`이 되는 문제를 방지함                        |

## 검증

- `.venv/bin/python -m py_compile server.py gunicorn_config.py`
- `python3 -m json.tool static/manifest.webmanifest >/dev/null`
- Flask test client smoke: `/`, `/manifest.webmanifest`, `/service-worker.js`, `/api/current`, `/api/push/config`
- 렌더링 HTML semantic check: 필수 DOM ID, `numberInput` text 입력, `window.CAFE701_CONFIG`, `formatToParts`, 누락 icon 참조 제거 확인

## 남은 위험

- 실제 iPhone Safari/PWA에서 시각·권한 흐름은 수동 확인 필요 — 홈 화면 추가 후 알림 권한/백그라운드 복귀를 테스트 권장.
- 작업 전부터 `.pi/tasks/2026-05-20-minimal-ui-refactor.md` 수정 및 `uv.lock` untracked 상태가 있었음 — 이번 변경과 별도로 정리 여부 결정 필요.

## 다음 단계

- [ ] iPhone Safari에서 390px 내외 화면, 홈 화면 PWA, 알림 권한 허용 흐름을 수동 테스트한다.
- [ ] 실제 주문번호 감시 시작/중지 및 SSE 재연결 문구를 운영 환경에서 확인한다.
