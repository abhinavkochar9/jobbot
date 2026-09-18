"""Auto-apply: Playwright form-fillers with hard safety gates.

Safety invariants (enforced here, not in the fillers):
- DRY_RUN=true → fill + screenshot, never submit.
- STOP file present → nothing runs.
- Daily/weekly caps and 90-day company+title dedupe checked before any fill.
- Any low-confidence screening answer, or any question about references,
  transcripts, or advisor contact → needs_review, never submitted.
- CAPTCHA or login wall → blocked, surfaced in digest. Never bypassed.
"""

import logging
import random
import time

from .. import config as cfg
from .. import db
from .runner import apply_to_posting

log = logging.getLogger(__name__)


def process_queue(config: dict, *, limit: int | None = None) -> list[dict]:
    """Apply to eligible postings above threshold, respecting all caps.

    Returns a list of result dicts for reporting.
    """
    if cfg.stop_requested():
        log.warning("STOP file present — application processing halted")
        return []

    if not config.get("auto_apply", False):
        log.info("auto_apply disabled in config — monitoring only, nothing submitted")
        return []


    threshold = config.get("score_threshold", 70)
    limits = config.get("limits", {})
    max_day = limits.get("max_submissions_per_day", 5)
    max_company_week = limits.get("max_per_company_per_week", 2)
    dedupe_days = limits.get("company_title_dedupe_days", 90)
    never = {c.lower() for c in
             cfg.load_profile().get("application_answers", {}).get("never_apply_companies", []) or []}

    results = []
    with db.get_db() as conn:
        candidates = conn.execute(
            "SELECT * FROM postings WHERE eligible=1 AND score>=? "
            "AND source NOT LIKE 'agg:%' ORDER BY score DESC", (threshold,)).fetchall()
        # right-swiped postings join the queue regardless of score
        swiped = conn.execute(
            "SELECT p.* FROM postings p JOIN swipes s ON s.posting_id=p.id "
            "WHERE s.decision='apply' AND p.eligible=1 AND p.score<? "
            "ORDER BY p.score DESC", (threshold,)).fetchall()
        # postings whose LATEST attempt is needs_review and whose questions are
        # all answered → retry. Latest-status check matters: a stale
        # needs_review row must never resurrect a posting that has since
        # been applied, blocked, or failed (cooldowns apply to those).
        # Retry rules: latest attempt is needs_review, all questions answered,
        # at most 4 total review attempts (then it parks as an app card), and
        # a 20h cooldown so an unconfirmable form gets one retry per day, not
        # one per cycle.
        from datetime import datetime, timedelta, timezone
        retry_cutoff = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        retry = conn.execute(
            "SELECT p.* FROM postings p WHERE p.eligible=1 "
            "AND (SELECT a.status FROM applications a WHERE a.posting_id=p.id "
            "     ORDER BY a.timestamp DESC LIMIT 1) = 'needs_review' "
            "AND (SELECT MAX(a1.timestamp) FROM applications a1 "
            "     WHERE a1.posting_id=p.id) < ? "
            "AND (SELECT COUNT(*) FROM applications a3 WHERE a3.posting_id=p.id "
            "     AND a3.status='needs_review') < 4 "
            "AND NOT EXISTS (SELECT 1 FROM applications a2 WHERE "
            "a2.posting_id=p.id AND a2.status='applied') "
            "AND NOT EXISTS (SELECT 1 FROM pending_questions q WHERE "
            "q.posting_id=p.id AND q.answer IS NULL)", (retry_cutoff,)).fetchall()
    retry_ids = {r["id"] for r in retry}
    candidates = list(candidates) + list(swiped) + list(retry)

    seen: set[int] = set()
    for row in candidates:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        if cfg.stop_requested():
            log.warning("STOP file appeared — halting queue mid-run")
            break
        with db.get_db() as conn:
            if db.submissions_today(conn) >= max_day:
                log.info("daily cap (%d) reached", max_day)
                break
            if row["company"].lower() in never:
                continue
            if db.swipe_decision(conn, row["id"]) == "skip":
                continue
            # blocked = portal we can't automate: attempt once, then it lives
            # as a manual card — never thrash it. failed = transient: retry
            # at most every 3 days.
            last = db.latest_application(conn, row["id"])
            if last and row["id"] not in retry_ids:
                if last["status"] == "blocked":
                    continue
                if last["status"] == "failed":
                    from datetime import datetime, timedelta, timezone
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
                    if last["timestamp"] > cutoff:
                        continue
            if row["id"] in retry_ids:
                pass  # answered needs_review → deliberate re-attempt
            elif db.already_applied(conn, row["id"], dry_run=cfg.dry_run()):
                continue
            if db.company_title_recent(conn, row["company"], row["title"], dedupe_days):
                continue
            if db.company_submissions_this_week(conn, row["company"]) >= max_company_week:
                continue

        result = apply_to_posting(dict(row), config)
        results.append(result)
        if limit and len(results) >= limit:
            break

        # polite random delay between applications
        delay = random.randint(limits.get("delay_min_seconds", 180),
                               limits.get("delay_max_seconds", 600))
        log.info("sleeping %ds before next application", delay)
        time.sleep(delay)

    return results
