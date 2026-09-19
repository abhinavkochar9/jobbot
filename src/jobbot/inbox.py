"""Close the loop on manually-submitted applications.

Kits are emailed with a posting marker in the subject (`[jobbot KIT #123]`).
When you reply "done" / "applied" / "submitted", this reads that reply over
IMAP and records the application — so the digest, the daily caps and the
90-day duplicate guard all know what you actually sent.

Reply vocabulary (first non-quoted line of your reply):
    done · applied · submitted · sent      → recorded as applied
    skip · no · not applying · pass        → recorded as skipped (no re-kit)
Anything else is left alone and reported in the digest as unclassified.
"""

import email
import imaplib
import logging
import os
import re
from email.header import decode_header, make_header

from . import config as cfg
from . import db

log = logging.getLogger(__name__)

KIT_SUBJECT_RE = re.compile(r"\[jobbot KIT #(\d+)\]")
APPLIED_RE = re.compile(r"^\W*(done|applied|submitted|sent|yes)\b", re.IGNORECASE)
SKIP_RE = re.compile(r"^\W*(skip|skipped|no|not applying|pass|ignore)\b", re.IGNORECASE)

# Lines that mean we've reached the quoted original — anything after is not
# the user's words and must never be matched (the kit itself contains "done").
QUOTE_RE = re.compile(r"^\s*(>|On .{0,80}wrote:|-{2,} ?Original Message|From:\s)", re.IGNORECASE)


def first_reply_line(body: str) -> str:
    """The user's own words: text before any quoted original."""
    for raw in (body or "").splitlines():
        line = raw.strip()
        if QUOTE_RE.match(raw):
            break
        if line:
            return line[:200]
    return ""


def _body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace")
    except Exception:  # noqa: BLE001
        return str(msg.get_payload())


def poll(mark_seen: bool = True) -> list[dict]:
    """Read unseen replies to kit emails and record the outcomes."""
    host = os.environ.get("IMAP_HOST", "imap.gmail.com")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (user and password):
        log.info("inbox: no SMTP_USER/SMTP_PASSWORD — reply tracking disabled")
        return []

    results = []
    try:
        M = imaplib.IMAP4_SSL(host, timeout=45)
        M.login(user, password)
        M.select("INBOX")
        typ, data = M.search(None, '(UNSEEN SUBJECT "jobbot KIT")')
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        log.info("inbox: %d unseen kit replies", len(ids))
        for num in ids:
            typ, raw = M.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            m = KIT_SUBJECT_RE.search(subject)
            if not m:
                continue
            posting_id = int(m.group(1))
            line = first_reply_line(_body_text(msg))
            if APPLIED_RE.match(line):
                outcome = "applied"
            elif SKIP_RE.match(line):
                outcome = "skipped"
            else:
                outcome = "unclear"
            results.append({"posting_id": posting_id, "outcome": outcome,
                            "said": line, "subject": subject[:90]})
            if outcome != "unclear":
                _record(posting_id, outcome, line)
            if mark_seen and outcome != "unclear":
                M.store(num, "+FLAGS", "\\Seen")
        M.close(); M.logout()
    except Exception as exc:  # noqa: BLE001
        log.error("inbox poll failed: %s", exc)
    return results


def _record(posting_id: int, outcome: str, said: str) -> None:
    with db.get_db() as conn:
        row = conn.execute("SELECT company, title FROM postings WHERE id=?",
                           (posting_id,)).fetchone()
        if not row:
            log.warning("inbox: reply for unknown posting #%s", posting_id)
            return
        if outcome == "applied":
            if conn.execute("SELECT 1 FROM applications WHERE posting_id=? AND "
                            "status='applied'", (posting_id,)).fetchone():
                return  # already recorded
            mat = conn.execute(
                "SELECT path FROM kits WHERE posting_id=?", (posting_id,)).fetchone()
            conn.execute(
                "INSERT INTO applications (posting_id, status, timestamp, "
                "materials_path, notes) VALUES (?,?,?,?,?)",
                (posting_id, "applied", db.now_iso(),
                 (mat["path"] if mat else None),
                 f"submitted by hand from the kit; you replied {said!r}"))
            log.info("recorded APPLIED by hand: %s — %s", row["company"], row["title"][:50])
        else:
            conn.execute(
                "INSERT INTO swipes (posting_id, decision, timestamp) VALUES (?,?,?) "
                "ON CONFLICT(posting_id) DO UPDATE SET decision=excluded.decision",
                (posting_id, "skip", db.now_iso()))
            log.info("recorded SKIP: %s — %s", row["company"], row["title"][:50])


def outstanding() -> list[dict]:
    """Kits sent but not yet answered — surfaced in the daily digest."""
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT k.sent_at, p.id, p.company, p.title, p.score, p.url FROM kits k "
            "JOIN postings p ON p.id=k.posting_id "
            "WHERE NOT EXISTS (SELECT 1 FROM applications a WHERE a.posting_id=p.id "
            "                  AND a.status='applied') "
            "AND NOT EXISTS (SELECT 1 FROM swipes s WHERE s.posting_id=p.id "
            "                AND s.decision='skip') "
            "ORDER BY p.score DESC")]
