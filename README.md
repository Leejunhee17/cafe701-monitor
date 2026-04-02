# ☕ Cafe 701 번호 알림

Cafe 701 주문 번호가 화면에 표시되면 아이폰으로 알림을 보내주는 웹 앱입니다.

## 동작 원리

1. `hanwha701.com/api/cafe701`에서 8초마다 카페 디스플레이 이미지를 가져옴
2. OCR.space API로 화면에 표시된 주문 번호를 인식
3. 사용자가 입력한 번호가 감지되면 **소리 + 진동 + 브라우저 알림** 발송

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
http://<컴퓨터 IP>:5001
```

## 웹 배포 (Render)

### 1. Render 계정 생성

[render.com](https://render.com) 에서 무료 계정 생성 (GitHub 로그인 가능)

### 2. New Web Service 생성

- **Repository**: `Leejunhee17/cafe701-monitor`
- **Runtime**: Python 3
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn --worker-class gevent --workers 1 --bind 0.0.0.0:$PORT server:app`

또는 저장소의 `render.yaml`이 자동으로 설정합니다.

### 3. 환경 변수 설정 (선택)

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `OCR_API_KEY` | OCR.space API 키 | `helloworld` (데모) |
| `POLL_INTERVAL` | 확인 주기 (초) | `8` |

> OCR.space 무료 계정 키 발급: [ocr.space/ocrapi](https://ocr.space/ocrapi) (월 25,000회)

## 기술 스택

- **Backend**: Python / Flask
- **OCR**: [OCR.space](https://ocr.space) API
- **Frontend**: Vanilla JS + SSE(Server-Sent Events)
- **배포**: Render
