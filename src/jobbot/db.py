"""SQLite state: postings, applications, screening log, watcher health."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    location TEXT,
    description TEXT,
    score INTEGER,
    score_breakdown TEXT,
    eligible INTEGER DEFAULT 1,
    ineligible_reason TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    status TEXT NOT NULL CHECK(status IN
        ('queued','applied','needs_review','blocked','failed','skipped')),
    timestamp TEXT NOT NULL,
    materials_path TEXT,
    evidence_path TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS screening_log (
    id INTEGER PRIMARY KEY,
    application_id INTEGER REFERENCES applications(id),
    question TEXT NOT NULL,
    answer TEXT,
    confidence TEXT,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_answers (
    question_norm TEXT PRIMARY KEY,
    answer TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swipes (
    posting_id INTEGER PRIMARY KEY REFERENCES postings(id),
    decision TEXT NOT NULL CHECK(decision IN ('apply','skip')),
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_questions (
    id INTEGER PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    question TEXT NOT NULL,
    options_json TEXT,
    created TEXT NOT NULL,
    answer TEXT,
    answered_at TEXT,
    UNIQUE(posting_id, question)
);
CREATE TABLE IF NOT EXISTS kits (
    posting_id INTEGER PRIMARY KEY REFERENCES postings(id),
    sent_at TEXT NOT NULL,
    subject TEXT,
    path TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watcher_health (
    watcher TEXT PRIMARY KEY,
    last_run TEXT,
    last_success TEXT,
    last_error TEXT,
    postings_found INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(score DESC);
CREATE INDEX IF NOT EXISTS idx_apps_posting ON applications(posting_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_db():
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_posting(conn, *, source, company, title, url, location, description,
                   score, breakdown, eligible, ineligible_reason=None) -> tuple[int, bool]:
    """Insert or refresh a posting. Returns (posting_id, is_new)."""
    ts = now_iso()
    row = conn.execute("SELECT id FROM postings WHERE url = ?", (url,)).fetchone()
    if row:
        conn.execute(
            "UPDATE postings SET last_seen=?, score=?, score_breakdown=?, "
            "eligible=?, ineligible_reason=? WHERE id=?",
            (ts, score, breakdown, int(eligible), ineligible_reason, row["id"]))
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO postings (source, company, title, url, location, description, "
        "score, score_breakdown, eligible, ineligible_reason, first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (source, company, title, url, location, description, score, breakdown,
         int(eligible), ineligible_reason, ts, ts))
    return cur.lastrowid, True


def record_health(conn, watcher: str, *, ok: bool, error: str | None = None,
                  found: int = 0) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO watcher_health (watcher, last_run, last_success, last_error, postings_found) "
        "VALUES (?,?,?,?,?) ON CONFLICT(watcher) DO UPDATE SET last_run=excluded.last_run, "
        "last_success=CASE WHEN excluded.last_success IS NOT NULL THEN excluded.last_success "
        "ELSE watcher_health.last_success END, last_error=excluded.last_error, "
        "postings_found=excluded.postings_found",
        (watcher, ts, ts if ok else None, error, found))


def already_applied(conn, posting_id: int, *, dry_run: bool = True) -> bool:
    """A posting is off-limits when it was ever actually applied to, or when
    its LATEST attempt is waiting on review ('queued' counts only in dry-run —
    dry fills must not block a later live application). Historical
    needs_review rows superseded by newer attempts don't block."""
    if conn.execute("SELECT 1 FROM applications WHERE posting_id=? AND "
                    "status='applied'", (posting_id,)).fetchone():
        return True
    last = latest_application(conn, posting_id)
    if last is None:
        return False
    blocking = ("queued", "needs_review") if dry_run else ("needs_review",)
    return last["status"] in blocking


def company_title_recent(conn, company: str, title: str, days: int = 90) -> bool:
    """True if we applied to the same company+title within `days`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT 1 FROM applications a JOIN postings p ON p.id=a.posting_id "
        "WHERE p.company=? AND p.title=? AND a.status='applied' AND a.timestamp>?",
        (company, title, cutoff)).fetchone() is not None


def submissions_today(conn) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) c FROM applications WHERE status='applied' AND timestamp LIKE ?",
        (today + "%",)).fetchone()["c"]


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def norm_question(q: str) -> str:
    """Normalise a form label so the same question matches across employers.

    Strips the decorations ATS forms add — "(required)", "*", trailing
    punctuation — which otherwise make a stored answer miss and re-ask the
    user a question they have already answered."""
    import re
    q = q.strip().lower()
    q = re.sub(r"\s*\((required|optional)\)\s*", " ", q)
    q = re.sub(r"\s+", " ", q)
    return q.strip(" *:?.\t\n")[:300]


def get_user_answer(conn, question: str) -> str | None:
    row = conn.execute("SELECT answer FROM user_answers WHERE question_norm=?",
                       (norm_question(question),)).fetchone()
    return row["answer"] if row else None


def set_user_answer(conn, question: str, answer: str) -> None:
    conn.execute(
        "INSERT INTO user_answers (question_norm, answer, updated) VALUES (?,?,?) "
        "ON CONFLICT(question_norm) DO UPDATE SET answer=excluded.answer, "
        "updated=excluded.updated", (norm_question(question), answer, now_iso()))


def add_pending_question(conn, posting_id: int, question: str,
                         options_json: str | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pending_questions "
        "(posting_id, question, options_json, created) VALUES (?,?,?,?)",
        (posting_id, question, options_json, now_iso()))


def unanswered_questions(conn, posting_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM pending_questions WHERE posting_id=? "
        "AND answer IS NULL", (posting_id,)).fetchone()["c"]


def swipe_decision(conn, posting_id: int) -> str | None:
    row = conn.execute("SELECT decision FROM swipes WHERE posting_id=?",
                       (posting_id,)).fetchone()
    return row["decision"] if row else None


def latest_application_status(conn, posting_id: int) -> str | None:
    row = conn.execute(
        "SELECT status FROM applications WHERE posting_id=? "
        "ORDER BY timestamp DESC LIMIT 1", (posting_id,)).fetchone()
    return row["status"] if row else None


def latest_application(conn, posting_id: int):
    return conn.execute(
        "SELECT status, timestamp FROM applications WHERE posting_id=? "
        "ORDER BY timestamp DESC LIMIT 1", (posting_id,)).fetchone()


def company_submissions_this_week(conn, company: str) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return conn.execute(
        "SELECT COUNT(*) c FROM applications a JOIN postings p ON p.id=a.posting_id "
        "WHERE p.company=? AND a.status='applied' AND a.timestamp>?",
        (company, cutoff)).fetchone()["c"]
