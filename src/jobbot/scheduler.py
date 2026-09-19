"""Main 24/7 loop: discovery every N hours, application queue within caps,
daily digest, instant top-tier alerts. Designed to run under systemd with
auto-restart; any crash restarts clean because all state is in SQLite."""

import logging
import threading
import time
from datetime import date, datetime

from . import config as cfg
from . import notify
from .apply import process_queue
from .discovery import run_discovery

log = logging.getLogger(__name__)

DIGEST_HOUR = 7  # local server time


def run_forever() -> None:
    cfg.setup_logging("scheduler")
    config = cfg.load_config()
    interval = config.get("discovery_interval_hours", 2) * 3600
    last_digest_day: date | None = None

    # dashboard runs in-process on localhost
    threading.Thread(target=_dashboard_thread, daemon=True).start()

    log.info("scheduler started (DRY_RUN=%s, interval=%ss)", cfg.dry_run(), interval)
    while True:
        cycle_start = time.time()
        config = cfg.load_config()  # pick up config edits without restart

        try:
            summary = run_discovery(config)
            log.info("discovery: %d new, %d watchers failed",
                     len(summary["new"]), len(summary["watchers_failed"]))
            notify.check_top_tier_alerts(config, summary["new"])
        except Exception:  # noqa: BLE001
            log.exception("discovery cycle failed (loop continues)")

        try:
            if cfg.stop_requested():
                log.warning("STOP file present — skipping application processing")
            else:
                results = process_queue(config)
                for r in results:
                    log.info("application %s: %s — %s", r["status"], r["company"], r["title"])
        except Exception:  # noqa: BLE001
            log.exception("application processing failed (loop continues)")

        try:
            from .inbox import poll
            for r in poll():
                log.info("kit reply #%s: %s (%r)", r["posting_id"], r["outcome"], r["said"][:40])
        except Exception:  # noqa: BLE001
            log.exception("inbox poll failed (loop continues)")

        try:
            now = datetime.now()
            if now.hour >= DIGEST_HOUR and last_digest_day != now.date():
                if config.get("kits", {}).get("enabled", True):
                    from .kit import morning_kits
                    sent = morning_kits(config)
                    log.info("morning kits: %d sent", len(sent))
                notify.daily_digest(config)
                last_digest_day = now.date()
        except Exception:  # noqa: BLE001
            log.exception("digest failed (loop continues)")

        elapsed = time.time() - cycle_start
        sleep_for = max(60, interval - elapsed)
        log.info("cycle done in %.0fs — sleeping %.0fs", elapsed, sleep_for)
        time.sleep(sleep_for)


def _dashboard_thread() -> None:
    try:
        from .dashboard import serve
        serve()
    except Exception:  # noqa: BLE001
        log.exception("dashboard failed to start (loop unaffected)")


if __name__ == "__main__":
    run_forever()
