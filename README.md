# ☕ Cafe 701 번호 알림

Cafe 701 주문 번호가 화면에 표시되면 아이폰으로 알림을 보내주는 웹 앱입니다.

## 동작 원리

1. `hanwha701.com/api/cafe701`에서 카페 디스플레이 이미지를 가져옴
2. OCR.space API로 화면에 표시된 주문 번호를 인식
3. 홈 화면 PWA가 Push Subscription을 서버에 저장하고, 사용자가 입력한 번호를 서버-side watch로 등록
4. 번호가 감지되면 서버가 Web Push를 보내 iPhone 잠금화면에 알림 표시
5. 앱이 열려 있을 때는 SSE로 현재 번호/상태를 같이 갱신

## 스크린샷

> 번호 입력 → 알림 대기 → 번호 감지 시 알림

## 로컬 실행

```bash
git clone https://github.com/Leejunhee17/cafe701-monitor.git
cd cafe701-monitor
pip install -r requirements.txt
python3 server.py
```

같은 Wi-Fi에서 아이폰 Safari로 접속:

```
http://<컴퓨터 IP>:8080
```

Web Push를 테스트하려면 HTTPS가 필요합니다. iOS에서는 Safari 공유 버튼 → **홈 화면에 추가** 후 홈 화면 앱에서 알림을 허용해야 합니다.

### VAPID 키 생성

```bash
python -m pywebpush --gen-vapid
```

출력된 public/private key를 각각 `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`로 설정하세요.

## 웹 배포 (Render)

### 1. Render 계정 생성

[render.com](https://render.com) 에서 무료 계정 생성 (GitHub 로그인 가능)

### 2. New Web Service 생성

- **Repository**: `Leejunhee17/cafe701-monitor`
- **Runtime**: Python 3
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn --config python:gunicorn_config --worker-class gthread --workers 1 --threads 20 --bind 0.0.0.0:$PORT server:app`

또는 저장소의 `render.yaml`이 자동으로 설정합니다.

### 3. 환경 변수 설정 (선택)

| 변수명                   | 설명                                   | 기본값                     |
| ------------------------ | -------------------------------------- | -------------------------- |
| `OCR_API_KEY`            | OCR.space API 키                       | `helloworld` (데모)        |
| `POLL_INTERVAL`          | 대기 번호가 있을 때 확인 주기 (초)     | `8`                        |
| `IDLE_INTERVAL`          | 대기 번호가 없을 때 확인 주기 (초)     | `60`                       |
| `VAPID_PUBLIC_KEY`       | Web Push 공개 키                       | 필수                       |
| `VAPID_PRIVATE_KEY`      | Web Push 개인 키                       | 필수                       |
| `VAPID_CLAIM_SUB`        | VAPID 연락처 claim (`mailto:` 권장)    | `mailto:admin@example.com` |
| `PUSH_STATE_FILE`        | Push subscription/watch JSON 저장 경로 | `push_state.json`          |
| `PUSH_WATCH_TTL_SECONDS` | 서버 감시 자동 만료 시간               | `14400`                    |

> OCR.space 무료 계정 키 발급: [ocr.space/ocrapi](https://ocr.space/ocrapi) (월 25,000회)

## 기술 스택

- **Backend**: Python / Flask
- **OCR**: [OCR.space](https://ocr.space) API
- **Frontend**: Vanilla JS + PWA Service Worker + SSE(Server-Sent Events)
- **Push**: Web Push / VAPID (`pywebpush`)
- **배포**: Render
