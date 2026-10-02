"""Drain the job queue while there is work, then stop.

There is no always-on worker. One used to poll the jobs table every two
seconds, and that query alone kept a scale-to-zero database awake all month. It
was the whole Neon bill for a demo nobody was using (LESSONS.md, "A two-second
poll was the whole database bill").

Now the web process calls `kick()` at startup and after every enqueue. A kick
starts one background drain, or wakes the one already running. The drain runs
due jobs, sleeps until the next retry falls due, and exits when no job is
waiting. An idle deployment sends no queries at all, so Fly can stop the
machine and Neon can suspend the database.

    python -m knowledge_desk.worker            # drain until empty, then exit
    python -m knowledge_desk.worker --once     # one pass over due jobs, then exit

Safe to run alongside another drain (a second machine, a manual run): claims
use SKIP LOCKED.
"""

from __future__ import annotations

import sys
import threading
import traceback

from knowledge_desk import jobs
from knowledge_desk.accounts import purge_expired_sessions
from knowledge_desk.config import settings
from knowledge_desk.ingest import run_pending

# One drain thread per process. `_wake` cuts a retry wait short when new work
# arrives. `_lock` makes "the queue is empty, so stop" and "start a drain" one
# decision each, so a kick cannot land between them and be lost.
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None


def drain(once: bool = False) -> None:
    """Run jobs until none is due or will fall due, waiting out retry backoff.

    Expired sessions are swept once per drain. resolve_session refuses them on
    sight, so the sweep is housekeeping and can wait for the next upload or the
    next cold start.
    """
    global _thread
    purged = purge_expired_sessions()
    if purged:
        print(f"purged {purged} expired session(s)", flush=True)
    while True:
        _wake.clear()
        counts = run_pending()
        if counts["processed"]:
            print(
                f"processed={counts['processed']} succeeded={counts['succeeded']}"
                f" requeued={counts['requeued']} dead={counts['dead']}",
                flush=True,
            )
        if once:
            return
        with _lock:
            wait = jobs.seconds_until_due()
            if wait is None:
                if _thread is threading.current_thread():
                    _thread = None
                return
        _wake.wait(wait)


def _run() -> None:
    global _thread
    try:
        drain()
    except Exception:  # noqa: BLE001 - a dead drain must not stop the next kick
        # Jobs left queued here wait for the next kick: an upload or a restart.
        traceback.print_exc()
        with _lock:
            _thread = None


def kick() -> None:
    """Make sure a drain is running. Cheap to call after every enqueue."""
    global _thread
    if not settings.drain_in_process:
        return
    with _lock:
        _wake.set()
        if _thread is None:
            _thread = threading.Thread(target=_run, name="drain", daemon=True)
            _thread.start()


def main(argv: list[str]) -> int:
    drain(once="--once" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
