"""Application kits — the morning email that turns a posting into a 10-minute
manual task: the link, the attached tailored materials, and a field-by-field
answer sheet scanned from the real form.

Why this exists: the best-fit postings live behind account-walled portals
(Workday) and AI-use attestations (Waymo, Anthropic). Automation can find,
score, and prepare them; a human must submit. This module does everything
up to the submit button and mails it.
"""

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from . import config as cfg
from . import db, matching, notify
from .apply import screening
from .apply.forms import _identity_map, _is_required, _label_for
from .apply.runner import detect_ats
from .tailor import generate_materials

log = logging.getLogger(__name__)

def standard_sheet(profile: dict) -> list[tuple[str, str]]:
    """Generic answer sheet for portals we cannot scan (account walls). Every
    value comes from profile.yaml; nothing personal lives in code."""
    c, a = profile["contact"], profile["application_answers"]
    phd = profile["education"][0]
    prior = profile["education"][1] if len(profile["education"]) > 1 else {}
    first, *rest = c["name"].split()
    return [
        ("First / last name", f"{first} / {rest[-1] if rest else ''}"),
        ("Email", c["email"]), ("Phone", c["phone"]), ("Location", c["location"]),
        ("LinkedIn", c.get("linkedin", "")), ("GitHub / website", c.get("github", "")),
        ("School / degree / field", f"{phd['institution']} / {phd['degree']}"),
        ("Education expected end", f"{a.get('expected_phd_graduation_month', '')} {a.get('expected_phd_graduation', '')}".strip()),
        ("Prior degree", f"{prior.get('degree', '')}, {prior.get('institution', '')}, {prior.get('dates', '')}"
                         + (f", GPA {prior['gpa']}" if prior.get("gpa") else "")),
        ("Work authorization", a.get("work_authorization_status", "")),
        ("Will you require sponsorship now or in future?", "Yes" if a.get("requires_future_sponsorship") else "No"),
        ("Citizenship", a.get("citizenship", "")),
        ("Availability", f"{', '.join(a.get('target_internship_terms', []))}; {a.get('internship_duration', '')}"),
        ("Willing to relocate / work onsite?", a.get("onsite_willingness", "")),
        ("Office choice", a.get("office_choice_rule", "")),
        ("How did you hear about us?", a.get("how_heard_about_role", "")),
        ("Gender / race / veteran / disability", a.get("demographic_questions", "")),
    ]



# ---------------------------------------------------------------- scan ----

def scan_form(url: str, ats: str) -> tuple[list[dict], str | None, str]:
    """Open the form headlessly and enumerate its fields WITHOUT filling.
    Returns (fields, ai_policy_phrase, page_kind) where page_kind is
    'form' | 'login_wall' | 'closed' | 'unknown'."""
    from playwright.sync_api import sync_playwright
    fields: list[dict] = []
    policy, kind = None, "unknown"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 2000})
        try:
            u = url.rstrip("/")
            if ats == "lever" and not u.endswith("/apply"):
                u += "/apply"
            elif ats == "ashby" and not u.endswith("/application"):
                u += "/application"
            page.goto(u, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            for sel in ("button:has-text('Dismiss')", "button:has-text('Accept')",
                        "#onetrust-accept-btn-handler"):
                try:
                    el = page.locator(sel).first
                    if el.count() and el.is_visible():
                        el.click(timeout=1500); page.wait_for_timeout(400); break
                except Exception:  # noqa: BLE001
                    pass
            if page.locator("input[type='file'], form input[type='text']").count() == 0:
                for i in range(min(page.locator("a:has-text('Apply'), button:has-text('Apply')").count(), 6)):
                    el = page.locator("a:has-text('Apply'), button:has-text('Apply')").nth(i)
                    try:
                        if el.is_visible():
                            el.click(); page.wait_for_timeout(3000); break
                    except Exception:  # noqa: BLE001
                        continue
            body = ""
            try:
                body = page.inner_text("body")[:60000]
            except Exception:  # noqa: BLE001
                pass
            if re.search(r"no longer (open|accepting|available)|position (has been|is) (filled|closed)", body, re.I):
                return [], None, "closed"
            if re.search(r"sign in to apply|log in to apply|create an account to apply|create account", body, re.I) \
                    and page.locator("input[type='file']").count() == 0:
                return [], matching.prohibits_ai_assistance(body), "login_wall"
            policy = matching.prohibits_ai_assistance(body)

            if page.locator("input[type='file']").count():
                fields.append({"label": "Resume/CV upload", "kind": "file", "required": True, "options": []})
            controls = page.locator(
                "input:visible:not([type='file']):not([type='hidden']):not([type='submit']), "
                "textarea:visible, select:visible")
            seen = set()
            for i in range(min(controls.count(), 90)):
                el = controls.nth(i)
                try:
                    label = (_label_for(page, el) or "").strip()
                    if not label or label in seen:
                        continue
                    tag = el.evaluate("e => e.tagName.toLowerCase()")
                    typ = (el.get_attribute("type") or "").lower()
                    if typ in ("checkbox", "radio"):
                        continue
                    kind_ = "select" if tag == "select" else ("textarea" if tag == "textarea" else "text")
                    role = (el.get_attribute("role") or "").lower()
                    if role == "combobox":
                        kind_ = "select"
                    opts = []
                    if tag == "select":
                        opts = [o.strip() for o in el.locator("option").all_inner_texts()
                                if o.strip() and not o.strip().lower().startswith("select")][:25]
                    seen.add(label)
                    fields.append({"label": label, "kind": kind_, "required": _is_required(el, label),
                                   "options": opts})
                except Exception:  # noqa: BLE001
                    continue
            groups = page.locator("fieldset:visible, [role='radiogroup']:visible")
            for i in range(min(groups.count(), 30)):
                g = groups.nth(i)
                try:
                    legend = (g.locator("legend, [class*='label'], label").first.inner_text() or "").strip()
                    opts = [t.strip() for t in g.locator("label").all_inner_texts() if t.strip() and t.strip() != legend]
                    if legend and opts and legend not in seen:
                        seen.add(legend)
                        fields.append({"label": legend, "kind": "choice", "required": "*" in legend, "options": opts[:12]})
                except Exception:  # noqa: BLE001
                    continue
            kind = "form" if fields else "unknown"
        finally:
            browser.close()
    return fields, policy, kind


# ------------------------------------------------------------- answers ----

def _own_words_material(profile: dict, posting: dict) -> list[str]:
    """Profile bullets most relevant to this posting — raw material for the
    user to write from when the employer forbids AI-drafted answers."""
    text = f"{posting['title']} {posting.get('description') or ''}".lower()
    picks = []
    for pr in profile.get("projects", []):
        tags = " ".join(pr.get("tags", [])).replace("-", " ")
        if any(t in text for t in tags.split()):
            picks.append(f"{pr['name']}: {pr['bullets'][0]}")
    for pub in profile.get("publications", []):
        picks.append(f"Preprint: {pub['title']} ({pub['venue']})")
    return picks[:5] or [f"{pr['name']}: {pr['bullets'][0]}" for pr in profile["projects"][:2]]


def answer_sheet(fields: list[dict], posting: dict, materials: dict, profile: dict,
                 policy: str | None) -> list[dict]:
    """One row per form field: what to put, and where it came from."""
    identity = _identity_map(profile)
    job_context = (f"Company: {posting['company']}\nRole: {posting['title']}\n"
                   f"Job location: {posting.get('location') or 'not stated'}")
    try:
        cover = materials["cover_letter"].read_text()
    except Exception:  # noqa: BLE001
        cover = None
    rows = []
    budget = 15
    for f in fields:
        label, kind_, opts = f["label"], f["kind"], f.get("options") or []
        row = {"label": label, "required": f["required"], "kind": kind_}
        if kind_ == "file":
            row.update(value="Upload the attached " + Path(_attachments(materials, policy)[0]).name + (" (your own CV)" if policy else ""), src="attach")
        elif re.search(r"cover\s*letter", label, re.I):
            row.update(value="Paste cover_letter.txt (attached)" if not policy else
                       "Write your own — see MATERIAL section", src="attach" if not policy else "own")
        elif re.search(r"research\s+(statement|interests?)", label, re.I) and kind_ == "textarea":
            row.update(value="Paste research_statement.txt (attached)" if not policy else
                       "Write your own — see MATERIAL section", src="attach" if not policy else "own")
        else:
            matched = next((v for pat, v in identity if pat.search(label)), None)
            if matched is not None:
                row.update(value=(matched[0] if isinstance(matched, list) else matched), src="profile")
            else:
                with db.get_db() as conn:
                    stored = db.get_user_answer(conn, label)
                if stored:
                    row.update(value=stored, src="your answer bank")
                elif policy and kind_ == "textarea":
                    row.update(value="Write in your own words — see MATERIAL section", src="own")
                elif budget > 0:
                    budget -= 1
                    try:
                        qa = screening.answer_question(label, options=opts or None,
                                                       cover_letter=cover, job_context=job_context)
                        row.update(value=qa.get("answer", ""), src="suggested",
                                   confidence=qa.get("confidence", "low"))
                    except Exception as exc:  # noqa: BLE001
                        row.update(value=f"(could not draft: {exc})", src="you", confidence="low")
                else:
                    row.update(value="(your call)", src="you", confidence="low")
        if opts and row.get("src") in ("profile", "your answer bank", "suggested"):
            row["options"] = opts
        rows.append(row)
    return rows


# -------------------------------------------------------------- render ----

def render_kit(posting: dict, n: int, total: int, fields, rows, materials, policy, kind) -> tuple[str, str]:
    bd = {}
    try:
        bd = json.loads(posting.get("score_breakdown") or "{}")
    except Exception:  # noqa: BLE001
        pass
    NICE = {"world": "world models", "imitation": "imitation / robot learning", "interpretab": "interpretability",
            "information": "learning theory", "large": "LLMs", "vision": "vision-language", "robot": "robotics",
            "reinforcement": "RL", "agentic": "agents / RAG", "computer": "computer vision", "deep": "deep learning",
            "quantum": "quantum ML", "diffusion": "generative models", "biomechanic": "healthcare / biomechanics"}
    hits = ", ".join(dict.fromkeys(next((v for k, v in NICE.items() if h.lower().lstrip("(?:").startswith(k)), "ML")
                                   for h in bd.get("keyword_hits", [])[:6]))
    subject = (f"[jobbot KIT #{posting['id']}] {posting['company']} — "
               f"{posting['title'][:55]} (score {posting['score']}, {n}/{total})")
    L = []
    L.append(f"{posting['company']} — {posting['title']}")
    L.append(f"Score {posting['score']}/100 · {posting.get('location') or 'location n/a'}")
    if hits: L.append(f"Why it fits: {hits}")
    L.append("")
    L.append(f"APPLY HERE:  {posting['url']}")
    L.append("")
    if policy:
        L.append("!! THIS EMPLOYER RESTRICTS AI-ASSISTED APPLICATIONS. The form asks you to attest to it.")
        L.append(f'   Their words: "{policy}"')
        L.append("   So: your OWN CV is attached (not a generated one), and free-text answers are")
        L.append("   left for you to write — source material is at the bottom of this email.")
        L.append("")
    if kind == "login_wall" or (not rows and re.search(r"workday|smartrecruiters|myworkdayjobs", posting.get("url", "") + posting.get("source", ""), re.I)):
        L.append(f"PORTAL NEEDS AN ACCOUNT (Workday/SmartRecruiters style). Create it with {cfg.load_profile()['contact']['email']},")
        L.append("then use the STANDARD ANSWER SHEET below — these portals ask the same fields every time.")
        L.append("")
    L.append("ATTACHED: " + ", ".join(Path(f).name for f in _attachments(materials, policy)))
    L.append("")
    L.append("STEP BY STEP")
    cv_name = Path(_attachments(materials, policy)[0]).name
    L.append(f" 1. Open the link. Upload {cv_name} where it asks for Resume/CV.")
    if not policy:
        L.append(" 2. Where a cover letter or statement is asked, paste the attached .txt.")
    L.append(f" {'3' if not policy else '2'}. Fill the fields exactly as below. Rows marked (suggested, LOW) are your call.")
    L.append(f" {'4' if not policy else '3'}. Submit, then REPLY to this email with just: done")
    L.append("   (reply 'skip' instead and it won't be offered again)")
    L.append("")
    if rows:
        L.append("FIELD-BY-FIELD (scanned from the live form)")
        for r in rows:
            req = "*" if r.get("required") else " "
            tag = {"attach": "attach", "profile": "profile", "your answer bank": "your rule",
                   "suggested": f"suggested, {r.get('confidence','low').upper()}", "own": "OWN WORDS",
                   "you": "your call"}.get(r.get("src"), r.get("src"))
            L.append(f" {req} {r['label'][:90]}")
            L.append(f"      → {str(r.get('value',''))[:400]}   [{tag}]")
            if r.get("options"):
                L.append(f"      options: {' | '.join(o[:28] for o in r['options'][:8])}")
    else:
        L.append("STANDARD ANSWER SHEET (form could not be scanned)")
        for k, v in standard_sheet(cfg.load_profile()):
            L.append(f"   {k}: {v}")
    L.append("")
    if policy:
        L.append("MATERIAL FOR YOUR OWN ANSWERS (from your record; rephrase, don't paste)")
        for m in _own_words_material(cfg.load_profile(), posting):
            L.append(f"   • {m[:300]}")
        L.append("")
    desc = (posting.get("description") or "").strip()
    if desc:
        L.append("JOB DESCRIPTION (first 1500 chars)")
        L.append(desc[:1500])
    return subject, "\n".join(L)


def _attachments(materials: dict, policy: str | None) -> list[str]:
    own_cv = cfg.DATA_DIR / "own_cv.pdf"  # your real CV, for employers that restrict AI-assisted applications
    files = []
    if policy and own_cv.exists():
        files.append(str(own_cv))
    else:
        files.append(str(materials["resume_pdf"]))
    if not policy:
        files.append(str(materials["cover_letter"]))
        if materials.get("research_statement"):
            files.append(str(materials["research_statement"]))
    return files


# ---------------------------------------------------------------- send ----

def build_and_send(posting: dict, n: int, total: int) -> dict:
    profile = cfg.load_profile()
    materials = generate_materials(posting)
    ats = detect_ats(posting["url"])
    fields, policy, kind = [], None, "unknown"
    try:
        fields, policy, kind = scan_form(posting["url"], ats)
    except Exception as exc:  # noqa: BLE001
        log.warning("scan failed for %s: %s", posting["url"], exc)
    if policy is None:
        policy = matching.prohibits_ai_assistance(posting.get("description") or "")
    rows = answer_sheet(fields, posting, materials, profile, policy) if fields else []
    subject, body = render_kit(posting, n, total, fields, rows, materials, policy, kind)
    outdir = Path(materials["dir"])
    (outdir / "KIT.txt").write_text(subject + "\n\n" + body)
    ok = notify.deliver_with_files(subject, body, _attachments(materials, policy))
    with db.get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO kits (posting_id, sent_at, subject, path) VALUES (?,?,?,?)",
                     (posting["id"], db.now_iso(), subject, str(outdir / "KIT.txt")))
    return {"posting_id": posting["id"], "subject": subject, "emailed": ok, "kind": kind,
            "fields": len(fields), "policy": bool(policy)}


def candidates(config: dict, limit: int) -> list[dict]:
    thr = config.get("kits", {}).get("threshold", 65)
    never = {c.lower() for c in cfg.load_profile().get("application_answers", {}).get("never_apply_companies", []) or []}
    out, seen_ct = [], set()
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT p.* FROM postings p WHERE p.eligible=1 AND p.score>=? "
            "AND p.source NOT LIKE 'agg:%' "
            "AND NOT EXISTS (SELECT 1 FROM kits k WHERE k.posting_id=p.id) "
            "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.posting_id=p.id AND a.status='applied') "
            "AND NOT EXISTS (SELECT 1 FROM swipes s WHERE s.posting_id=p.id AND s.decision='skip') "
            "ORDER BY p.score DESC, p.first_seen DESC", (thr,)).fetchall()
    with db.get_db() as conn:  # roles already kitted, so a duplicate listing isn't re-sent
        for k in conn.execute("SELECT p.company, p.title FROM kits k JOIN postings p "
                              "ON p.id=k.posting_id"):
            seen_ct.add((k["company"].lower(), re.sub(r"\W+", "", k["title"].lower())[:60]))
    for r in rows:
        if r["company"].lower() in never:
            continue  # Anthropic-style: draft kit only, never a generated kit
        key = (r["company"].lower(), re.sub(r"\W+", "", r["title"].lower())[:60])
        if key in seen_ct:
            continue
        seen_ct.add(key)
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def morning_kits(config: dict) -> list[dict]:
    """Send the day's batch of kits, highest score first."""
    per_day = config.get("kits", {}).get("per_day", 5)
    from datetime import date as _date
    with db.get_db() as conn:
        sent_today = conn.execute(
            "SELECT COUNT(*) c FROM kits WHERE substr(sent_at,1,10)=?",
            (_date.today().isoformat(),)).fetchone()["c"]
    remaining = max(0, per_day - sent_today)
    if remaining == 0:
        log.info("kits: daily cap of %d already sent today", per_day)
        return []
    batch = candidates(config, remaining)
    results = []
    for i, p in enumerate(batch, 1):
        try:
            results.append(build_and_send(p, i, len(batch)))
            log.info("kit %d/%d sent: %s — %s", i, len(batch), p["company"], p["title"][:50])
        except Exception:  # noqa: BLE001
            log.exception("kit failed for %s", p["url"])
    return results


if __name__ == "__main__":
    import sys
    cfg.setup_logging("kits")
    c = cfg.load_config()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else c.get("kits", {}).get("per_day", 5)
    c.setdefault("kits", {})["per_day"] = n
    for r in morning_kits(c):
        print(json.dumps(r))
