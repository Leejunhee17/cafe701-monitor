import sys
import io
import os
import time
import queue
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

# 로그 즉시 출력 (버퍼링 비활성화)
sys.stdout.reconfigure(line_buffering=True)
import threading
import requests
from flask import Flask, render_template, Response, jsonify, stream_with_context
from PIL import Image

KST = ZoneInfo("Asia/Seoul")
OPEN_HOUR  = int(os.environ.get("OPEN_HOUR",  "7"))   # 07:00 KST
CLOSE_HOUR = int(os.environ.get("CLOSE_HOUR", "17"))  # 17:00 KST
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "8"))   # 대기자 있을 때 (초)
IDLE_INTERVAL = int(os.environ.get("IDLE_INTERVAL", "60"))  # 대기자 없을 때 (초)

CAFE_API_URL = "https://www.hanwha701.com/api/cafe701"
OCR_API_URL  = "https://api.ocr.space/parse/image"
OCR_API_KEY  = os.environ.get("OCR_API_KEY", "helloworld")

app = Flask(__name__)

# Active monitors: { number_str: [queue, ...] }
monitors: dict[str, list[queue.Queue]] = {}
monitors_lock = threading.Lock()

# OCR 캐시
_ocr_cache: dict = {"phash": None, "numbers": []}


def is_operating_hours() -> bool:
    hour = datetime.now(KST).hour
    return OPEN_HOUR <= hour < CLOSE_HOUR


def _phash(img: Image.Image, size: int = 16) -> bytes:
    """16×16 흑백 썸네일 퍼셉추얼 해시."""
    small = img.convert("L").resize((size, size), Image.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    return bytes(1 if p >= avg else 0 for p in pixels)


def _phash_similar(h1: bytes, h2: bytes, threshold: int = 8) -> bool:
    return sum(a != b for a, b in zip(h1, h2)) < threshold


def fetch_image_bytes() -> bytes:
    resp = requests.post(CAFE_API_URL, data="test", timeout=10)
    resp.raise_for_status()
    return resp.content


def extract_numbers(img_bytes: bytes, force: bool = False) -> list[str]:
    """이미지에서 주문 번호 추출. force=True 시 캐시 무시."""
    global _ocr_cache
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    # 주문 번호 패널만 크롭 (우측 안내/시간 패널 제외)
    left, top, right, bottom = int(w * 0.28), int(h * 0.10), int(w * 0.57), int(h * 0.68)
    cropped = img.crop((left, top, right, bottom))

    # 퍼셉추얼 해시로 변화 감지 (force=True면 건너뜀)
    current_hash = _phash(cropped)
    if not force and _ocr_cache["phash"] is not None and _phash_similar(current_hash, _ocr_cache["phash"]):
        print(f"[ocr] 이미지 변화 없음, 캐시 반환: {_ocr_cache['numbers']}")
        return _ocr_cache["numbers"]

    # 500px로 리사이즈 후 OCR
    ratio = 500 / cropped.width
    cropped = cropped.resize((500, int(cropped.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=80)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    resp = requests.post(OCR_API_URL, data={
        "apikey": OCR_API_KEY,
        "base64Image": "data:image/jpeg;base64," + img_b64,
        "language": "eng",
        "scale": True,
        "OCREngine": 1,
    }, timeout=15)

    result = resp.json()
    if not isinstance(result, dict):
        print(f"[ocr] 비정상 응답: {str(result)[:200]}")
        return _ocr_cache["numbers"]

    parsed = result.get("ParsedResults", [{}])[0].get("ParsedText", "")
    numbers = [t.strip() for t in parsed.split() if t.strip().isdigit() and 1 <= len(t.strip()) <= 4]
    _ocr_cache["phash"] = current_hash
    _ocr_cache["numbers"] = numbers
    print(f"[ocr] 새 OCR 결과: {numbers}")
    return numbers


def monitor_loop():
    """운영시간 중 항상 폴링: 대기자 있으면 8초, 없으면 60초 간격."""
    print(f"[monitor] 스레드 시작")
    tick = 0
    while True:
        tick += 1

        if not is_operating_hours():
            print(f"[monitor] 운영시간 외 ({datetime.now(KST).strftime('%H:%M')} KST)")
            time.sleep(POLL_INTERVAL)
            continue

        with monitors_lock:
            has_watchers = bool(monitors)
            watcher_list = list(monitors.keys())

        print(f"[monitor] tick={tick} watchers={watcher_list}")

        try:
            img_bytes = fetch_image_bytes()
            print(f"[monitor] 이미지 수신 ({len(img_bytes)} bytes)")
            numbers = extract_numbers(img_bytes)

            if has_watchers:
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

        # 대기자 있을 때 8초, 없을 때 60초
        time.sleep(POLL_INTERVAL if has_watchers else IDLE_INTERVAL)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/watch/<number>")
def watch(number: str):
    """SSE endpoint: 번호 감지 시 알림."""
    number = number.strip()
    q: queue.Queue = queue.Queue(maxsize=50)

    with monitors_lock:
        monitors.setdefault(number, []).append(q)

    def generate():
        import json
        print(f"[sse] {number}번 연결됨", flush=True)
        try:
            yield f"data: {json.dumps({'status': 'watching', 'number': number})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=3)
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("found"):
                        break
                except queue.Empty:
                    yield 'data: {"ping": true}\n\n'
        finally:
            print(f"[sse] {number}번 연결 종료", flush=True)
            with monitors_lock:
                if number in monitors:
                    try:
                        monitors[number].remove(q)
                    except ValueError:
                        pass
                    if not monitors[number]:
                        del monitors[number]

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.route("/api/current")
def current():
    """캐시된 번호 즉시 반환 (OCR 호출 없음)."""
    if not is_operating_hours():
        return jsonify({"closed": True, "open_hour": OPEN_HOUR, "close_hour": CLOSE_HOUR})
    return jsonify({"numbers": _ocr_cache["numbers"]})


@app.route("/api/refresh")
def refresh_numbers():
    """강제 새로고침: 캐시 무시하고 새로 OCR."""
    if not is_operating_hours():
        return jsonify({"closed": True})
    try:
        img_bytes = fetch_image_bytes()
        numbers = extract_numbers(img_bytes, force=True)
        return jsonify({"numbers": numbers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    threading.Thread(target=monitor_loop, daemon=True).start()
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
    print(f"  서버 시작! {scheme}://{local_ip}:{port}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_context)
