"""Background worker: drain the job queue, then wait and repeat. Runs as its own
process alongside the API (a second service in compose and in the deploy).

    python -m knowledge_desk.worker            # loop forever
    python -m knowledge_desk.worker --once     # drain once and exit

Idempotent and safe to run more than one at a time: claims use SKIP LOCKED.
"""

from __future__ import annotations

import sys
import time

from knowledge_desk.accounts import purge_expired_sessions
from knowledge_desk.ingest import run_pending

POLL_SECONDS = 2.0
# Expired sessions are refused on sight, so sweeping them is housekeeping and
# an hour of staleness costs nothing. The worker is the process already awake
# on a timer, which is the only reason this lives here.
SESSION_PURGE_SECONDS = 3600.0


def loop(once: bool = False) -> None:
    next_purge = 0.0
    while True:
        counts = run_pending()
        if counts["processed"]:
            print(
                f"processed={counts['processed']} succeeded={counts['succeeded']}"
                f" requeued={counts['requeued']} dead={counts['dead']}",
                flush=True,
            )
        now = time.monotonic()
        if now >= next_purge:
            purged = purge_expired_sessions()
            if purged:
                print(f"purged {purged} expired session(s)", flush=True)
            next_purge = now + SESSION_PURGE_SECONDS
        if once:
            return
        time.sleep(POLL_SECONDS)


def main(argv: list[str]) -> int:
    loop(once="--once" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
