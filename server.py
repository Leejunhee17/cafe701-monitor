import base64
import hashlib
import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import (  # type: ignore[reportMissingImports]
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
)
from PIL import Image

# 로그 즉시 출력 (버퍼링 비활성화)
_stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_stdout_reconfigure):
    _stdout_reconfigure(line_buffering=True)

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - local dev without optional deps installed yet
    WebPushException = Exception
    webpush = None

KST = ZoneInfo("Asia/Seoul")
OPEN_HOUR = int(os.environ.get("OPEN_HOUR", "7"))  # 07:00 KST
CLOSE_HOUR = int(os.environ.get("CLOSE_HOUR", "17"))  # 17:00 KST
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "8"))  # 대기자 있을 때 (초)
IDLE_INTERVAL = int(os.environ.get("IDLE_INTERVAL", "60"))  # 대기자 없을 때 (초)

CAFE_API_URL = "https://www.hanwha701.com/api/cafe701"
OCR_API_URL = "https://api.ocr.space/parse/image"
OCR_API_KEY = os.environ.get("OCR_API_KEY", "helloworld")

app = Flask(__name__)

PUSH_STATE_FILE = os.environ.get("PUSH_STATE_FILE", "/tmp/cafe701-push-state.json")
PUSH_WATCH_TTL_SECONDS = int(os.environ.get("PUSH_WATCH_TTL_SECONDS", "14400"))
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIM_SUB = os.environ.get("VAPID_CLAIM_SUB", "mailto:admin@example.com")

# Foreground SSE monitors: { number_str: [queue, ...] }
sse_monitors: dict[str, list[queue.Queue]] = {}
monitors_lock = threading.Lock()

# Server-side Web Push state. Kept separate from SSE so iOS can sleep.
push_lock = threading.RLock()
monitor_wake_event = threading.Event()
push_state: dict = {"subscriptions": {}, "watches": {}}

# OCR 캐시
_ocr_cache: dict = {"phash": None, "numbers": []}


def is_operating_hours() -> bool:
    hour = datetime.now(KST).hour
    return OPEN_HOUR <= hour < CLOSE_HOUR


def _now_ts() -> int:
    return int(time.time())


def _normalize_number(number: object) -> str | None:
    value = str(number or "").strip()
    if value.isdigit() and 1 <= len(value) <= 4:
        return value
    return None


def _subscription_id(subscription: dict) -> str:
    endpoint = str(subscription.get("endpoint", ""))
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:24]


def _valid_subscription(subscription: object) -> bool:
    if not isinstance(subscription, dict):
        return False
    keys = subscription.get("keys")
    return bool(
        subscription.get("endpoint")
        and isinstance(keys, dict)
        and keys.get("p256dh")
        and keys.get("auth")
    )


def _load_push_state() -> None:
    global push_state
    if not os.path.exists(PUSH_STATE_FILE):
        return
    try:
        with open(PUSH_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("push state is not an object")
        subscriptions = data.get("subscriptions", {})
        watches = data.get("watches", {})
        if not isinstance(subscriptions, dict) or not isinstance(watches, dict):
            raise ValueError("push state has invalid shape")
        for subscription in subscriptions.values():
            if not _valid_subscription(subscription):
                raise ValueError("push state has invalid subscription")
        for watch in watches.values():
            if not isinstance(watch, dict):
                raise ValueError("push state has invalid watch")
            if not _normalize_number(watch.get("number")):
                raise ValueError("push state has invalid watch number")
            if watch.get("subscription_id") not in subscriptions:
                raise ValueError("push state references missing subscription")
        with push_lock:
            push_state = {"subscriptions": subscriptions, "watches": watches}
            _prune_expired_watches_locked(save=False)
        print(
            f"[push] state loaded subscriptions={len(subscriptions)} watches={len(watches)}"
        )
    except Exception as e:
        with push_lock:
            push_state = {"subscriptions": {}, "watches": {}}
        print(f"[push] state load failed; starting empty: {e}")


def _save_push_state_locked() -> None:
    directory = os.path.dirname(PUSH_STATE_FILE) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".push-state-", suffix=".json", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(push_state, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, PUSH_STATE_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _prune_expired_watches_locked(save: bool = True) -> int:
    now = _now_ts()
    watches = push_state.setdefault("watches", {})
    expired = [
        watch_id
        for watch_id, watch in watches.items()
        if int(watch.get("expires_at", 0)) <= now
    ]
    for watch_id in expired:
        watches.pop(watch_id, None)
    if expired and save:
        _save_push_state_locked()
    return len(expired)


def _store_subscription_locked(subscription: dict) -> str:
    subscription_id = _subscription_id(subscription)
    push_state.setdefault("subscriptions", {})[subscription_id] = subscription
    _save_push_state_locked()
    return subscription_id


def _delete_subscription_locked(subscription_id: str) -> int:
    removed = 0
    if (
        push_state.setdefault("subscriptions", {}).pop(subscription_id, None)
        is not None
    ):
        removed += 1
    watches = push_state.setdefault("watches", {})
    for watch_id, watch in list(watches.items()):
        if watch.get("subscription_id") == subscription_id:
            watches.pop(watch_id, None)
            removed += 1
    if removed:
        _save_push_state_locked()
    return removed


def _active_push_watch_numbers_locked() -> list[str]:
    _prune_expired_watches_locked()
    return sorted(
        {str(watch.get("number")) for watch in push_state.get("watches", {}).values()}
    )


def _upsert_watch_locked(number: str, subscription_id: str) -> tuple[str, bool]:
    now = _now_ts()
    expires_at = now + PUSH_WATCH_TTL_SECONDS
    watches = push_state.setdefault("watches", {})
    for watch_id, watch in watches.items():
        if (
            watch.get("number") == number
            and watch.get("subscription_id") == subscription_id
        ):
            watch.update(
                {"created_at": now, "expires_at": expires_at, "notified_at": None}
            )
            _save_push_state_locked()
            return watch_id, False
    watch_id = uuid.uuid4().hex
    watches[watch_id] = {
        "number": number,
        "subscription_id": subscription_id,
        "created_at": now,
        "expires_at": expires_at,
        "notified_at": None,
    }
    _save_push_state_locked()
    return watch_id, True


def _collect_push_notifications(numbers: list[str]) -> list[dict]:
    notifications = []
    with push_lock:
        _prune_expired_watches_locked()
        watches = push_state.setdefault("watches", {})
        subscriptions = push_state.setdefault("subscriptions", {})
        seen: set[tuple[str, str]] = set()
        for watch_id, watch in list(watches.items()):
            number = str(watch.get("number", ""))
            subscription_id = str(watch.get("subscription_id", ""))
            if number not in numbers:
                continue
            dedup_key = (subscription_id, number)
            subscription = subscriptions.get(subscription_id)
            if subscription and dedup_key not in seen:
                notifications.append(
                    {
                        "watch_id": watch_id,
                        "number": number,
                        "subscription_id": subscription_id,
                        "subscription": subscription,
                        "numbers": numbers,
                    }
                )
                seen.add(dedup_key)
    return notifications


def _remove_push_watch_locked(watch_id: str) -> None:
    if push_state.setdefault("watches", {}).pop(watch_id, None) is not None:
        _save_push_state_locked()


def _send_push_notification(item: dict) -> bool:
    if not webpush or not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        print("[push] skipped: VAPID keys or pywebpush are missing")
        return False
    payload = json.dumps(
        {
            "title": "☕ 커피 준비 완료!",
            "body": f"{item['number']}번 주문이 나왔습니다!",
            "number": item["number"],
            "watchId": item["watch_id"],
            "url": "/",
        },
        ensure_ascii=False,
    )
    try:
        webpush(
            subscription_info=item["subscription"],
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_SUB},
            ttl=3600,
        )
        print(
            f"[push] sent number={item['number']} subscription={item['subscription_id']}"
        )
        return True
    except WebPushException as e:
        response = getattr(e, "response", None)
        status_code = getattr(response, "status_code", None)
        print(f"[push] send failed number={item['number']} status={status_code}: {e}")
        if status_code in (404, 410):
            with push_lock:
                _delete_subscription_locked(item["subscription_id"])
            return True
        return False
    except Exception as e:
        print(f"[push] send failed number={item['number']}: {type(e).__name__}: {e}")
        return False


_load_push_state()


def _phash(img: Image.Image, size: int = 16) -> bytes:
    """16×16 흑백 썸네일 퍼셉추얼 해시."""
    small = img.convert("L").resize((size, size), Image.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    return bytes(1 if p >= avg else 0 for p in pixels)


def _phash_similar(h1: bytes, h2: bytes, threshold: int = 8) -> bool:
    return sum(a != b for a, b in zip(h1, h2, strict=False)) < threshold


def fetch_image_bytes() -> bytes:
    resp = requests.post(CAFE_API_URL, data="test", timeout=10)
    resp.raise_for_status()
    return resp.content


def extract_numbers(img_bytes: bytes, force: bool = False) -> list[str]:
    """이미지에서 주문 번호 추출. force=True 시 캐시 무시."""
    global _ocr_cache
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    # 주문 번호 패널만 크롭 (우측 안내/시간 패널 제외) - 누락 방지를 위해 마진 확대
    left, top, right, bottom = (
        int(w * 0.24),
        int(h * 0.08),
        int(w * 0.60),
        int(h * 0.70),
    )
    cropped = img.crop((left, top, right, bottom))

    # 퍼셉추얼 해시로 변화 감지 (force=True면 건너뜀)
    current_hash = _phash(cropped)
    if (
        not force
        and _ocr_cache["phash"] is not None
        and _phash_similar(current_hash, _ocr_cache["phash"])
    ):
        print(f"[ocr] 이미지 변화 없음, 캐시 반환: {_ocr_cache['numbers']}")
        return _ocr_cache["numbers"]

    # 흑백(Grayscale) 변환 및 대비(Contrast) 3배 향상으로 OCR 인식 성능 비약적 개선
    from PIL import ImageEnhance
    processed = cropped.convert("L")
    enhancer = ImageEnhance.Contrast(processed)
    processed = enhancer.enhance(3.0)

    # 500px로 리사이즈 후 OCR
    ratio = 500 / processed.width
    processed = processed.resize((500, int(processed.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    processed.save(buf, format="JPEG", quality=80)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    resp = requests.post(
        OCR_API_URL,
        data={
            "apikey": OCR_API_KEY,
            "base64Image": "data:image/jpeg;base64," + img_b64,
            "language": "eng",
            "scale": True,
            "OCREngine": 2,
            "isTable": True,
        },
        timeout=15,
    )

    result = resp.json()
    if not isinstance(result, dict):
        print(f"[ocr] 비정상 응답: {str(result)[:200]}")
        return _ocr_cache["numbers"]

    parsed = result.get("ParsedResults", [{}])[0].get("ParsedText", "")
    numbers = [
        t.strip()
        for t in parsed.split()
        if t.strip().isdigit() and 1 <= len(t.strip()) <= 4
    ]
    _ocr_cache["phash"] = current_hash
    _ocr_cache["numbers"] = numbers
    print(f"[ocr] 새 OCR 결과: {numbers}")
    return numbers


def _watcher_snapshot() -> tuple[list[str], list[str]]:
    with monitors_lock:
        sse_watcher_list = list(sse_monitors.keys())
    with push_lock:
        push_watcher_list = _active_push_watch_numbers_locked()
    return sse_watcher_list, push_watcher_list


def _sleep_until_next_poll(has_watchers: bool) -> None:
    interval = POLL_INTERVAL if has_watchers else IDLE_INTERVAL
    monitor_wake_event.wait(interval)
    monitor_wake_event.clear()


def _broadcast_sse_numbers(numbers: list[str]) -> None:
    with monitors_lock:
        found_targets = []
        for target, queues in list(sse_monitors.items()):
            found = target in numbers
            print(f"[monitor] sse target={target} found={found}")
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
        for target in found_targets:
            sse_monitors.pop(target, None)
            print(f"[monitor] {target}번 발견! SSE 모니터 제거")


def _send_pending_push_notifications(numbers: list[str]) -> None:
    for notification in _collect_push_notifications(numbers):
        if _send_push_notification(notification):
            with push_lock:
                _remove_push_watch_locked(notification["watch_id"])


def _poll_once(tick: int) -> bool:
    sse_watcher_list, push_watcher_list = _watcher_snapshot()
    has_watchers = bool(sse_watcher_list or push_watcher_list)
    print(f"[monitor] tick={tick} sse={sse_watcher_list} push={push_watcher_list}")

    img_bytes = fetch_image_bytes()
    print(f"[monitor] 이미지 수신 ({len(img_bytes)} bytes)")
    numbers = extract_numbers(img_bytes)

    if sse_watcher_list:
        _broadcast_sse_numbers(numbers)
    _send_pending_push_notifications(numbers)
    return has_watchers


def monitor_loop():
    """운영시간 중 항상 폴링: SSE와 서버-side push watch를 분리해 감시."""
    print("[monitor] 스레드 시작")
    tick = 0
    while True:
        tick += 1

        if not is_operating_hours():
            print(f"[monitor] 운영시간 외 ({datetime.now(KST).strftime('%H:%M')} KST)")
            _sleep_until_next_poll(has_watchers=True)
            continue

        has_watchers = False
        try:
            has_watchers = _poll_once(tick)
        except Exception as e:
            import traceback

            print(f"[monitor] 오류: {e}")
            print(traceback.format_exc())
            sse_watcher_list, push_watcher_list = _watcher_snapshot()
            has_watchers = bool(sse_watcher_list or push_watcher_list)

        # 대기자 있을 때 8초, 없을 때 60초. 새 push watch 등록 시 즉시 깨움.
        _sleep_until_next_poll(has_watchers)


@app.route("/")
def index():
    return render_template(
        "index.html",
        open_hour=OPEN_HOUR,
        close_hour=CLOSE_HOUR,
        poll_interval=POLL_INTERVAL,
        timezone="Asia/Seoul",
    )


@app.route("/service-worker.js")
def service_worker():
    static_folder = app.static_folder or "static"
    response = send_from_directory(
        static_folder, "service-worker.js", mimetype="application/javascript"
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/manifest.webmanifest")
def webmanifest():
    static_folder = app.static_folder or "static"
    return send_from_directory(
        static_folder, "manifest.webmanifest", mimetype="application/manifest+json"
    )


@app.route("/api/push/config")
def push_config():
    return jsonify(
        {
            "supported": bool(webpush and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY),
            "publicKey": VAPID_PUBLIC_KEY,
            "watchTtlSeconds": PUSH_WATCH_TTL_SECONDS,
        }
    )


@app.route("/api/push/subscriptions", methods=["POST"])
def create_push_subscription():
    data = request.get_json(silent=True) or {}
    subscription = data.get("subscription")
    if not _valid_subscription(subscription):
        return jsonify({"error": "invalid_subscription"}), 400
    with push_lock:
        subscription_id = _store_subscription_locked(
            subscription if isinstance(subscription, dict) else {}
        )
    return jsonify({"subscriptionId": subscription_id})


@app.route("/api/push/subscriptions/<subscription_id>", methods=["DELETE"])
def delete_push_subscription(subscription_id: str):
    with push_lock:
        removed = _delete_subscription_locked(subscription_id)
    return jsonify({"removed": removed})


@app.route("/api/push/watches", methods=["POST"])
def create_push_watch():
    data = request.get_json(silent=True) or {}
    number = _normalize_number(data.get("number"))
    subscription = data.get("subscription")
    subscription_id = str(data.get("subscriptionId") or "")

    if not number:
        return jsonify({"error": "invalid_number"}), 400
    if not webpush or not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return jsonify({"error": "push_not_configured"}), 503

    with push_lock:
        if subscription is not None:
            if not _valid_subscription(subscription):
                return jsonify({"error": "invalid_subscription"}), 400
            subscription_id = _store_subscription_locked(
                subscription if isinstance(subscription, dict) else {}
            )
        elif subscription_id not in push_state.get("subscriptions", {}):
            return jsonify({"error": "unknown_subscription"}), 404
        watch_id, created = _upsert_watch_locked(number, subscription_id)
        watch = push_state["watches"][watch_id]

    monitor_wake_event.set()
    return jsonify(
        {
            "watchId": watch_id,
            "subscriptionId": subscription_id,
            "number": number,
            "expiresAt": watch["expires_at"],
        }
    ), 201 if created else 200


@app.route("/api/push/watches/<watch_id>", methods=["DELETE"])
def delete_push_watch(watch_id: str):
    with push_lock:
        removed = push_state.setdefault("watches", {}).pop(watch_id, None) is not None
        if removed:
            _save_push_state_locked()
    return jsonify({"removed": removed})


@app.route("/api/watch/<number>")
def watch(number: str):
    """SSE endpoint: 번호 감지 시 알림."""
    number = _normalize_number(number)
    if not number:
        return jsonify({"error": "invalid_number"}), 400
    q: queue.Queue = queue.Queue(maxsize=50)

    with monitors_lock:
        sse_monitors.setdefault(number, []).append(q)

    def generate():
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
                if number in sse_monitors:
                    if q in sse_monitors[number]:
                        sse_monitors[number].remove(q)
                    if not sse_monitors[number]:
                        del sse_monitors[number]

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/current")
def current():
    """캐시된 번호 즉시 반환 (OCR 호출 없음)."""
    if not is_operating_hours():
        return jsonify(
            {"closed": True, "open_hour": OPEN_HOUR, "close_hour": CLOSE_HOUR}
        )
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
    parser.add_argument(
        "--port", "-p", type=int, default=int(os.environ.get("PORT", 8080))
    )
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
    ssl_context = (
        (args.ssl_cert, args.ssl_key) if args.ssl_cert and args.ssl_key else None
    )
    scheme = "https" if ssl_context else "http"
    print("=" * 50)
    print(f"  서버 시작! {scheme}://{local_ip}:{port}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_context)
