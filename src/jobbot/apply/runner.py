"""Drive one application end-to-end: materials → fill → gate → (submit)."""

import json
import logging
import re
from datetime import date

from .. import config as cfg
from .. import db
from ..tailor import generate_materials
from . import forms

log = logging.getLogger(__name__)


def detect_ats(url: str) -> str:
    if "greenhouse.io" in url or "gh_jid" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "ashbyhq.com" in url:
        return "ashby"
    return "generic"


def apply_to_posting(posting: dict, config: dict) -> dict:
    """Returns {"posting_id", "company", "title", "status", "notes",
    "evidence", "materials"}. Status: applied | needs_review | blocked | failed
    (or 'filled_dry_run' when DRY_RUN kept us from submitting)."""
    result = {"posting_id": posting["id"], "company": posting["company"],
              "title": posting["title"], "url": posting["url"],
              "status": "failed", "notes": "", "evidence": None, "materials": None}
    try:
        materials = generate_materials(posting)
        result["materials"] = str(materials["dir"])
    except Exception as exc:  # noqa: BLE001
        result["notes"] = f"material generation failed: {exc}"
        _record(result)
        return result

    ats = detect_ats(posting["url"])
    try:
        fill = forms.fill_application(
            ats=ats, posting=posting, materials=materials, config=config)
        result.update(fill)
    except Exception as exc:  # noqa: BLE001
        log.exception("form fill failed for %s", posting["url"])
        result["status"] = "failed"
        result["notes"] = f"form fill error: {exc}"

    _record(result)
    return result


def _record(result: dict) -> None:
    status_map = {"filled_dry_run": "queued"}  # dry-run fills stay queued
    status = status_map.get(result["status"], result["status"])
    with db.get_db() as conn:
        if result.get("closed"):
            conn.execute(
                "UPDATE postings SET eligible=0, ineligible_reason='posting closed' "
                "WHERE id=?", (result["posting_id"],))
        cur = conn.execute(
            "INSERT INTO applications (posting_id, status, timestamp, "
            "materials_path, evidence_path, notes) VALUES (?,?,?,?,?,?)",
            (result["posting_id"], status, db.now_iso(), result.get("materials"),
             result.get("evidence"), result.get("notes")))
        app_id = cur.lastrowid
        for qa in result.get("screening_qa", []):
            conn.execute(
                "INSERT INTO screening_log (application_id, question, answer, "
                "confidence, timestamp) VALUES (?,?,?,?,?)",
                (app_id, qa["question"], qa.get("answer"), qa.get("confidence"),
                 db.now_iso()))
            # low-confidence answers on REQUIRED fields become question cards
            # in the swipe app; optional fields are left blank, never asked
            if (status == "needs_review" and qa.get("confidence") != "high"
                    and qa.get("required")):
                db.add_pending_question(
                    conn, result["posting_id"], qa["question"],
                    json.dumps(qa.get("options")) if qa.get("options") else None)

    if status == "applied":
        from .. import notify
        notify.deliver(
            f"[jobbot] APPLIED: {result['company']} — {result['title']}",
            f"Submitted application:\n\n{result['company']} — {result['title']}\n"
            f"{result['url']}\n\nMaterials: {result.get('materials')}\n"
            f"Confirmation screenshot: {result.get('evidence')}\n"
            f"Notes: {result.get('notes')}")
