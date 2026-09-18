"""Email digests and instant alerts. Falls back to reports/ files when SMTP
credentials are absent."""

import logging
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from . import config as cfg
from . import db

log = logging.getLogger(__name__)


def _send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("DIGEST_TO", user)
    if not (host and user and password and to):
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("email send failed: %s", exc)
        return False


def _send_email_with_files(subject: str, body: str, files: list) -> bool:
    host = os.environ.get("SMTP_HOST"); user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD"); to = os.environ.get("DIGEST_TO", user)
    if not (host and user and password and to):
        return False
    msg = MIMEMultipart()
    msg["Subject"] = subject; msg["From"] = user; msg["To"] = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for f in files:
        f = Path(f)
        if not f.exists():
            continue
        part = MIMEApplication(f.read_bytes(), Name=f.name)
        part["Content-Disposition"] = f'attachment; filename="{f.name}"'
        msg.attach(part)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=60) as s:
            s.starttls(); s.login(user, password); s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("email (with attachments) send failed: %s", exc)
        return False


def deliver_with_files(subject: str, body: str, files: list) -> bool:
    """Email with attachments; falls back to a report file listing them."""
    if _send_email_with_files(subject, body, files):
        log.info("emailed (+%d files): %s", len(files), subject)
        return True
    deliver(subject, body + "\n\nATTACHMENTS (on server):\n" + "\n".join(str(f) for f in files))
    return False


def deliver(subject: str, body: str) -> None:
    """Email if configured, else write to reports/."""
    if _send_email(subject, body):
        log.info("emailed: %s", subject)
        return
    fname = cfg.REPORTS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{subject[:40].replace(' ', '_').replace('/', '-')}.txt"
    fname.parent.mkdir(parents=True, exist_ok=True)
    fname.write_text(f"{subject}\n{'=' * len(subject)}\n\n{body}")
    log.info("wrote report %s", fname)


def instant_alert(posting: dict) -> None:
    body = (f"Top-tier posting detected:\n\n{posting['company']} — {posting['title']}\n"
            f"Score: {posting['score']}\nURL: {posting['url']}\n\n"
            f"Apply window for research internships can be days — review soon.")
    deliver(f"[jobbot ALERT] {posting['company']}: {posting['title']}", body)


def daily_digest(config: dict) -> str:
    """Compose + deliver the daily digest. Returns the body text."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    threshold = config.get("score_threshold", 70)
    lines = [f"jobbot daily digest — {date.today().isoformat()}", ""]

    with db.get_db() as conn:
        new = conn.execute(
            "SELECT * FROM postings WHERE first_seen>? ORDER BY score DESC", (cutoff,)).fetchall()
        lines.append(f"NEW POSTINGS (last 24h): {len(new)}")
        for r in new:
            flag = " *thin-description*" if "thin_description" in (r["score_breakdown"] or "") else ""
            el = "" if r["eligible"] else f" [INELIGIBLE: {r['ineligible_reason']}]"
            lines.append(f"  [{r['score']:>3}] {r['company']} — {r['title']}{flag}{el}\n        {r['url']}")

        for status, header in (("applied", "SUBMITTED"), ("needs_review", "NEEDS YOUR REVIEW"),
                               ("blocked", "BLOCKED (apply manually)"), ("failed", "FAILURES"),
                               ("queued", "QUEUED (dry-run filled)")):
            rows = conn.execute(
                "SELECT a.*, p.company, p.title, p.url FROM applications a "
                "JOIN postings p ON p.id=a.posting_id WHERE a.status=? AND a.timestamp>?",
                (status, cutoff)).fetchall()
            lines.append(f"\n{header}: {len(rows)}")
            for r in rows:
                lines.append(f"  {r['company']} — {r['title']}\n        {r['url']}\n        {r['notes'] or ''}")

        lines.append("\nWATCHER HEALTH:")
        for r in conn.execute("SELECT * FROM watcher_health ORDER BY watcher").fetchall():
            mark = "OK " if r["last_error"] is None else "ERR"
            lines.append(f"  {mark} {r['watcher']:<36} found={r['postings_found']} "
                         f"{('— ' + r['last_error'][:80]) if r['last_error'] else ''}")

    lines.append("\nMANUAL WATCH (no reliable API — check these yourself):")
    for m in config.get("manual_watch", []):
        lines.append(f"  {m['company']}: {m['url']}")

    body = "\n".join(lines)
    deliver(f"[jobbot] daily digest {date.today().isoformat()}", body)
    return body


def check_top_tier_alerts(config: dict, new_postings: list[dict]) -> None:
    top = {c.lower() for c in config.get("top_tier", [])}
    threshold = config.get("score_threshold", 70)
    for p in new_postings:
        if p["company"].lower() in top and p.get("eligible") and p["score"] >= threshold - 10:
            instant_alert(p)
