import sys
import io
import os
import time
import queue
import base64

# 로그 즉시 출력 (버퍼링 비활성화)
sys.stdout.reconfigure(line_buffering=True)
import threading
import requests
from flask import Flask, render_template, Response, jsonify
from PIL import Image

app = Flask(__name__)

CAFE_API_URL = "https://www.hanwha701.com/api/cafe701"
OCR_API_URL = "https://api.ocr.space/parse/image"
OCR_API_KEY = os.environ.get("OCR_API_KEY", "helloworld")  # 무료 계정: https://ocr.space/ocrapi
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "8"))  # seconds

# Active monitors: { number_str: [queue, ...] }
monitors: dict[str, list[queue.Queue]] = {}
monitors_lock = threading.Lock()


def fetch_image_bytes() -> bytes:
    resp = requests.post(CAFE_API_URL, data="test", timeout=10)
    resp.raise_for_status()
    return resp.content


def extract_numbers(img_bytes: bytes) -> list[str]:
    """Crop display area and OCR via ocr.space API."""
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    # Crop to the display screen area (center of the camera image)
    left, top, right, bottom = int(w * 0.28), int(h * 0.12), int(w * 0.75), int(h * 0.60)
    cropped = img.crop((left, top, right, bottom))

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=90)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    resp = requests.post(OCR_API_URL, data={
        "apikey": OCR_API_KEY,
        "base64Image": "data:image/jpeg;base64," + img_b64,
        "language": "eng",
        "scale": True,
        "OCREngine": 2,
    }, timeout=15)

    result = resp.json()
    parsed = result.get("ParsedResults", [{}])[0].get("ParsedText", "")
    # 1~4자리 숫자 (너무 긴 노이즈 제거)
    numbers = [t.strip() for t in parsed.split() if t.strip().isdigit() and 1 <= len(t.strip()) <= 4]
    return numbers


def monitor_loop():
    """Background thread: polls cafe API and notifies watchers."""
    print(f"[monitor] 스레드 시작 (간격: {POLL_INTERVAL}초)")
    tick = 0
    while True:
        tick += 1
        with monitors_lock:
            has_watchers = bool(monitors)
            watcher_list = list(monitors.keys())

        print(f"[monitor] tick={tick} watchers={watcher_list}")

        if has_watchers:
            try:
                print(f"[monitor] 이미지 가져오는 중...")
                img_bytes = fetch_image_bytes()
                print(f"[monitor] 이미지 수신 ({len(img_bytes)} bytes), OCR 시작...")
                numbers = extract_numbers(img_bytes)
                print(f"[monitor] OCR 결과: {numbers}")

                with monitors_lock:
                    found_targets = []
                    for target, queues in monitors.items():
                        found = target in numbers
                        print(f"[monitor] target={target} found={found}")
                        msg = {"found": found, "numbers": numbers}
                        dead = []
                        for q in queues:
                            try:
                                q.put_nowait(msg)
                            except queue.Full:
                                dead.append(q)
                        for q in dead:
                            queues.remove(q)
                        if found:
                            found_targets.append(target)
                    for t in found_targets:
                        del monitors[t]
                        print(f"[monitor] {t}번 발견! 모니터 제거")

            except Exception as e:
                import traceback
                print(f"[monitor] 오류: {e}")
                print(traceback.format_exc())
        else:
            print(f"[monitor] 대기자 없음, 스킵")

        time.sleep(POLL_INTERVAL)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/watch/<number>")
def watch(number: str):
    """SSE endpoint: streams status updates for the given number."""
    number = number.strip()
    q: queue.Queue = queue.Queue(maxsize=50)

    with monitors_lock:
        monitors.setdefault(number, []).append(q)

    def generate():
        import json
        try:
            yield f"data: {json.dumps({'status': 'watching', 'number': number})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("found"):
                        break
                except queue.Empty:
                    yield 'data: {"ping": true}\n\n'
        finally:
            with monitors_lock:
                if number in monitors:
                    try:
                        monitors[number].remove(q)
                    except ValueError:
                        pass
                    if not monitors[number]:
                        del monitors[number]

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/current")
def current():
    """One-shot check: returns currently displayed numbers."""
    try:
        img_bytes = fetch_image_bytes()
        numbers = extract_numbers(img_bytes)
        return jsonify({"numbers": numbers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# gunicorn 포함 모든 실행 방식에서 모니터 스레드 시작
threading.Thread(target=monitor_loop, daemon=True).start()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("PORT", 8080)))
    parser.add_argument("--ssl-cert", default=os.environ.get("SSL_CERT"))
    parser.add_argument("--ssl-key", default=os.environ.get("SSL_KEY"))
    args = parser.parse_args()

    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    port = args.port
    ssl_context = (args.ssl_cert, args.ssl_key) if args.ssl_cert and args.ssl_key else None
    scheme = "https" if ssl_context else "http"
    print("=" * 50)
    print(f"  서버 시작!")
    print(f"  아이폰에서 접속: {scheme}://{local_ip}:{port}")
    print(f"  (같은 와이파이에 연결되어 있어야 합니다)")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_context)
