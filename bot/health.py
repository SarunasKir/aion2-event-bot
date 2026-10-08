"""Health check: notices when the bot's check loop stops running.

The check loop calls beat() every 20 seconds. A background thread exits the
process if no beat arrives for HEALTH_TIMEOUT seconds, so Docker's or
systemd's restart policy starts a fresh bot instead of leaving a frozen one.
The beat is also written to HEALTH_FILE, which `python -m bot.health` reads
for Docker's HEALTHCHECK.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("aion2bot.health")

HEALTH_FILE = os.getenv("HEALTH_FILE", "/tmp/aion2bot.heartbeat")
TIMEOUT = int(os.getenv("HEALTH_TIMEOUT", "300"))

_last_beat: float | None = None


def beat() -> None:
    global _last_beat
    _last_beat = time.monotonic()
    try:
        with open(HEALTH_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def is_stale(last_beat: float | None, now: float, timeout: int = TIMEOUT) -> bool:
    """True once the loop has started and then gone quiet for longer than `timeout`."""
    return last_beat is not None and now - last_beat > timeout


def start_watchdog() -> None:
    """Exit the process if the check loop freezes, so the host restarts the bot."""
    def watch() -> None:
        while True:
            time.sleep(30)
            if is_stale(_last_beat, time.monotonic()):
                log.critical("No check loop activity for %ss; exiting so the bot restarts.", TIMEOUT)
                logging.shutdown()
                os._exit(1)

    threading.Thread(target=watch, name="watchdog", daemon=True).start()


def main() -> int:
    """Exit 0 if the heartbeat file is fresh, 1 otherwise (for Docker HEALTHCHECK)."""
    try:
        with open(HEALTH_FILE) as f:
            age = time.time() - int(f.read().strip())
    except (OSError, ValueError):
        print("no heartbeat yet")
        return 1
    print(f"last heartbeat {int(age)}s ago")
    return 0 if age <= 120 else 1


if __name__ == "__main__":
    sys.exit(main())
