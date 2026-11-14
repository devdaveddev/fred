"""Heavy work (training models, building the demo company) runs in a separate worker process.

Threads can't do this job: Python runs one thread's bytecode at a time, so a busy analysis thread makes page
requests wait (and lowering its priority makes it worse: it can be paused while holding the lock everyone needs).
A separate process is scheduled by the OS independently, at lower priority, so clicks stay instant while
the team works. One worker, so memory stays bounded; if it dies (say, out of memory) it's replaced.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .config import get_settings

log = logging.getLogger("fred.compute")
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()


def _init() -> None:
    try:
        os.nice(10)  # page requests (the parent process) come first
    except OSError:
        pass


def _executor() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"), initializer=_init)
        return _pool


def _reset() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def memory_limit_mb() -> int | None:
    """The container's memory limit (cgroup v2 or v1), if there is one."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 60:
            return int(raw) // (1024 * 1024)
    return None


_inline_warned = False


def _use_process() -> bool:
    """A second process needs roughly 450 MB more. On small plans run inline: slower clicks beat crashing."""
    global _inline_warned
    if not get_settings().compute_process:
        return False
    limit = memory_limit_mb()
    if limit is not None and limit < 1100:
        if not _inline_warned:
            log.warning("only %d MB of memory: running analyses in-process. Use a 2 GB instance for responsive "
                        "pages while the team works.", limit)
            _inline_warned = True
        return False
    return True


def _submit(fn, *args):
    if not _use_process():
        return fn(*args)  # tests, scripts, and small servers: run inline
    try:
        return _executor().submit(fn, *args).result()
    except BrokenProcessPool:
        log.error("the compute worker died (out of memory?); starting a new one")
        _reset()
        raise RuntimeError("The analysis worker stopped unexpectedly (the server may be out of memory).") from None


# ---------------- jobs (run inside the worker) ----------------
def _analyse(ws):
    from .team import run

    return run(ws)


def _seed(tid: str) -> None:
    from .db import engine
    from .seed import seed_workspace

    with engine.begin() as c:
        seed_workspace(c, tid)


def analyse(ws):
    return _submit(_analyse, ws)


def seed_demo(tid: str) -> None:
    _submit(_seed, tid)
