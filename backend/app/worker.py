"""Background worker: once a minute, run each workspace's daily job (sync sources, send the digest) when due.

    python -m app.worker           run forever
    python -m app.worker --once    one pass (cron-style deployments, tests)
    python -m app.worker --health  exit 0 if the loop is alive (container health check)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from .config import get_settings

HEARTBEAT = os.environ.get("WORKER_HEARTBEAT", "/tmp/fred-worker-heartbeat")
log = logging.getLogger("fred.worker")


def health() -> int:
    try:
        age = time.time() - os.path.getmtime(HEARTBEAT)
    except OSError:
        return 1
    return 0 if age < 3 * max(get_settings().worker_interval_s, 60) else 1


def main() -> int:
    if "--health" in sys.argv:
        return health()
    logging.basicConfig(level=get_settings().log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from .notify.jobs import tick

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker started (every %ss)", get_settings().worker_interval_s)
    while not stop.is_set():
        try:
            tick()
        except Exception:
            log.exception("scheduler pass failed")  # the DB may be restarting; try again next pass
        with open(HEARTBEAT, "w") as fh:
            fh.write(str(time.time()))
        if "--once" in sys.argv:
            break
        stop.wait(get_settings().worker_interval_s)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
