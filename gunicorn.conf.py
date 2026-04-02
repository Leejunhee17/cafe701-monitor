import threading


def post_fork(server, worker):
    """워커 프로세스 fork 후 모니터 스레드 시작."""
    from server import monitor_loop
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    server.log.info("monitor thread started in worker %s", worker.pid)
