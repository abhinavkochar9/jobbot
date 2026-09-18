"""Playwright form-fillers: Greenhouse, Lever, Ashby + generic best-effort.

All fillers share one flow:
  1. open posting URL (headless Chromium), find the application form
  2. CAPTCHA / login wall detected → status "blocked", screenshot, stop
  3. fill identity fields from profile.yaml; upload tailored resume;
     paste cover letter
  4. remaining questions → screening.answer_question(); any low confidence
     or always-review topic → status "needs_review", screenshot, DO NOT submit
  5. DRY_RUN → screenshot filled form, status "filled_dry_run"
     else → click submit, screenshot confirmation, status "applied"
"""

import logging
import re
from datetime import datetime

from .. import config as cfg
from .. import matching
from . import screening

log = logging.getLogger(__name__)

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']", ".g-recaptcha", "iframe[src*='hcaptcha']",
    ".h-captcha", "iframe[src*='turnstile']", "[data-captcha]",
]


def captcha_challenge_visible(page) -> bool:
    """True only when an INTERACTIVE captcha challenge is presented (checkbox
    widget or image grid). Invisible score-based captchas (reCAPTCHA v3 badge,
    passive hCaptcha) present no challenge and are not treated as a wall —
    we never attempt to solve or evade an actual challenge."""
    viewport = page.viewport_size or {"width": 1280}
    for sel in CAPTCHA_SELECTORS:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 5)):
            el = loc.nth(i)
            try:
                src = el.get_attribute("src") or ""
                if "size=invisible" in src:
                    continue  # score-based mode: no challenge is presented
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if not box:
                    continue
                if box["x"] + box["width"] > viewport["width"] + 10:
                    continue  # collapsed badge peeking off the right edge
                # v3 badge is ~70px wide; interactive widgets are 300+px
                if box["width"] >= 150 and box["height"] >= 60:
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False
LOGIN_HINTS = [
    "text=/sign in to apply/i", "text=/log in to apply/i",
    "text=/create an account to apply/i",
]

# label-pattern → profile value resolver
def _identity_map(profile: dict) -> list[tuple[re.Pattern, str]]:
    c = profile["contact"]
    ans = profile["application_answers"]
    first, *rest = c["name"].split()
    last = rest[-1] if rest else ""
    phd = profile["education"][0]
    return [
        (re.compile(r"first\s*name", re.I), first),
        (re.compile(r"last\s*name|family\s*name|surname", re.I), last),
        (re.compile(r"full\s*name|^name$", re.I), c["name"]),
        (re.compile(r"e-?mail", re.I), c["email"]),
        (re.compile(r"phone|mobile", re.I), c["phone"]),
        (re.compile(r"location|city|address", re.I), c["location"]),
        (re.compile(r"linkedin", re.I), c["linkedin"]),
        (re.compile(r"github", re.I), c["github"]),
        (re.compile(r"website|portfolio|url", re.I), c["github"]),
        (re.compile(r"school|university|institution", re.I), phd["institution"]),
        (re.compile(r"degree", re.I),
         ["PhD", "Doctorate", "Doctor of Philosophy", "Ph.D."]),
        (re.compile(r"(field|discipline|major)", re.I), "Computer Science"),
        (re.compile(r"graduat\w+.*month.*year|graduat\w+\s*date", re.I),
         f"{ans.get('expected_phd_graduation_month', '')} {ans['expected_phd_graduation']}".strip()),
        (re.compile(r"graduat\w+.*month|end\s+date\s+month", re.I),
         str(ans.get("expected_phd_graduation_month", ""))),
        (re.compile(r"graduat\w+.*year|end\s+date\s+year", re.I), str(ans["expected_phd_graduation"])),
        # bare "End date" in an ATS education block = degree completion.
        (re.compile(r"^end\s+date", re.I),
         f"{ans.get('expected_phd_graduation_month','')} {ans['expected_phd_graduation']}".strip()),
        (re.compile(r"current\s+(company|employer)", re.I), "University of Missouri-Kansas City"),
    ]


def _evidence_path(company: str, kind: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "_", company.lower())[:24]
    path = cfg.EVIDENCE_DIR / f"{ts}_{slug}_{kind}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def fill_application(*, ats: str, posting: dict, materials: dict, config: dict) -> dict:
    """Returns dict with status/notes/evidence/screening_qa."""
    from playwright.sync_api import sync_playwright

    profile = cfg.load_profile()
    result = {"status": "failed", "notes": "", "evidence": None, "screening_qa": []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 2000})
        try:
            # ATS form pages have stable URLs — go straight to the form
            url = posting["url"].rstrip("/")
            if ats == "lever" and not url.endswith("/apply"):
                url += "/apply"
            elif ats == "ashby" and not url.endswith("/application"):
                url += "/application"
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(3500)

            # dismiss cookie banners so they can't intercept clicks
            for sel in ("button:has-text('Dismiss')", "button:has-text('Accept')",
                        "button:has-text('OK')", "#onetrust-accept-btn-handler"):
                try:
                    el = page.locator(sel).first
                    if el.count() and el.is_visible():
                        el.click(timeout=2000)
                        page.wait_for_timeout(500)
                        break
                except Exception:  # noqa: BLE001
                    pass

            # if no form is present yet, click the first VISIBLE Apply control
            if page.locator("input[type='file'], form input[type='text']").count() == 0:
                candidates = page.locator(
                    "a:has-text('Apply'), button:has-text('Apply')")
                for i in range(min(candidates.count(), 6)):
                    el = candidates.nth(i)
                    try:
                        if el.is_visible():
                            el.click()
                            page.wait_for_timeout(3000)
                            break
                    except Exception:  # noqa: BLE001
                        continue

            # posting expired? (redirects to the board with a banner)
            closed = page.locator(
                "text=/no longer (open|accepting|available)|position (has been|is) "
                "(filled|closed)|job (is )?closed/i")
            if closed.count() > 0:
                result["status"] = "skipped"
                result["notes"] = "posting closed"
                result["closed"] = True
                result["evidence"] = _evidence_path(posting["company"], "closed")
                page.screenshot(path=result["evidence"], full_page=True)
                return result

            # Employer prohibits AI-assisted applications → never fill or
            # submit. These forms typically also require attesting to the
            # policy, so an automated submission would be a false attestation.
            try:
                page_text = page.inner_text("body")[:60000]
            except Exception:  # noqa: BLE001
                page_text = ""
            policy = matching.prohibits_ai_assistance(page_text)
            if policy:
                result["status"] = "blocked"
                result["notes"] = (f"employer restricts AI-assisted applications "
                                   f"(\"{policy}\") — apply manually")
                result["ai_policy"] = policy
                result["evidence"] = _evidence_path(posting["company"], "ai_policy")
                page.screenshot(path=result["evidence"], full_page=True)
                return result

            # Interactive CAPTCHA challenge / login wall → blocked (never solve)
            if captcha_challenge_visible(page):
                result["status"] = "blocked"
                result["notes"] = "interactive CAPTCHA challenge — apply manually"
                result["evidence"] = _evidence_path(posting["company"], "blocked")
                page.screenshot(path=result["evidence"], full_page=True)
                return result
            for sel in LOGIN_HINTS:
                if page.locator(sel).count() > 0:
                    result["status"] = "blocked"
                    result["notes"] = "login required — apply manually"
                    result["evidence"] = _evidence_path(posting["company"], "blocked")
                    page.screenshot(path=result["evidence"], full_page=True)
                    return result

            filled, review_reasons = _fill_fields(page, profile, materials, result, posting)

            if filled == 0:
                # No fillable form (portal wants an account, or layout unknown)
                # — a 0-field submit must never happen, live or dry.
                result["status"] = "blocked"
                result["notes"] = "no fillable form found — apply manually"
                result["evidence"] = _evidence_path(posting["company"], "blocked")
                page.screenshot(path=result["evidence"], full_page=True)
                return result

            if review_reasons:
                result["status"] = "needs_review"
                result["notes"] = "; ".join(review_reasons)[:500]
                result["evidence"] = _evidence_path(posting["company"], "needs_review")
                page.screenshot(path=result["evidence"], full_page=True)
                return result

            if cfg.dry_run():
                result["status"] = "filled_dry_run"
                result["notes"] = f"DRY_RUN: filled {filled} fields, not submitted"
                result["evidence"] = _evidence_path(posting["company"], "dry_run")
                page.screenshot(path=result["evidence"], full_page=True)
                return result

            # Live submit — and VERIFY it actually went through. "applied" is
            # only recorded on a positive success signal, never on a click alone.
            submits = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Submit application'), button:has-text('Submit')")
            submit = None
            for i in range(min(submits.count(), 5)):
                cand = submits.nth(i)
                try:
                    if cand.is_visible():
                        submit = cand
                        break
                except Exception:  # noqa: BLE001
                    continue
            if submit is None:
                result["status"] = "needs_review"
                result["notes"] = "no visible submit button found"
                result["evidence"] = _evidence_path(posting["company"], "needs_review")
                page.screenshot(path=result["evidence"], full_page=True)
                return result
            try:
                submit.scroll_into_view_if_needed(timeout=8000)
            except Exception:  # noqa: BLE001 — overlay in the way; JS-scroll
                submit.evaluate("e => e.scrollIntoView({block: 'center'})")
            if submit.is_disabled():
                result["status"] = "needs_review"
                result["notes"] = "submit button disabled — a required field is unsatisfied"
                result["evidence"] = _evidence_path(posting["company"], "needs_review")
                page.screenshot(path=result["evidence"], full_page=True)
                return result
            for attempt in range(2):
                submit.click()
                page.wait_for_timeout(7000)
                # a challenge appearing AT submit is a hard stop — never solve it
                if captcha_challenge_visible(page):
                    result["status"] = "blocked"
                    result["notes"] = "CAPTCHA challenge at submit — apply manually"
                    result["evidence"] = _evidence_path(posting["company"], "blocked")
                    page.screenshot(path=result["evidence"], full_page=True)
                    return result
                if _submission_confirmed(page):
                    result["status"] = "applied"
                    result["notes"] = f"submitted ({filled} fields, confirmed)"
                    result["evidence"] = _evidence_path(posting["company"], "confirmation")
                    page.screenshot(path=result["evidence"], full_page=True)
                    return result
            result["status"] = "needs_review"
            result["notes"] = ("submit clicked but no confirmation detected — "
                              "verify manually before assuming it went through")
            result["evidence"] = _evidence_path(posting["company"], "needs_review")
            page.screenshot(path=result["evidence"], full_page=True)
            return result
        finally:
            browser.close()


SUCCESS_PATTERNS = [
    "text=/thank you for (applying|your application)/i",
    "text=/application (has been |was )?(submitted|received)/i",
    "text=/we('|’)ve received your application/i",
    "text=/successfully submitted/i",
    "text=/you('|’)ve already applied/i",   # duplicate → it IS on file
]


def _submission_confirmed(page) -> bool:
    for sel in SUCCESS_PATTERNS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    # form replaced entirely (no submit button, no file input) also = success
    try:
        return (page.locator("button:has-text('Submit')").count() == 0
                and page.locator("input[type='file']").count() == 0)
    except Exception:  # noqa: BLE001
        return False


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _set_value(page, el, tag: str, value: str) -> bool:
    """Apply a value to a control. Handles native selects, ARIA comboboxes
    (Greenhouse job-boards style), and plain inputs. Returns True only when
    the value was genuinely applied — a combobox with no matching option
    returns False rather than guessing."""
    values = value if isinstance(value, list) else [value]
    value = str(values[0])
    target_n = _norm_text(value)
    if tag == "select":
        try:
            el.select_option(label=value)
            return True
        except Exception:  # noqa: BLE001
            pass
        try:  # fuzzy: match option text by normalized containment
            opts = el.locator("option")
            best = None
            for i in range(min(opts.count(), 60)):
                t_n = _norm_text(opts.nth(i).inner_text())
                if not t_n or t_n.startswith("select"):
                    continue
                if t_n == target_n:
                    best = i
                    break
                if best is None and (t_n in target_n or target_n in t_n):
                    best = i
            if best is not None:
                el.select_option(index=best)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False
    role = (el.get_attribute("role") or "").lower()
    aria = (el.get_attribute("aria-autocomplete") or "").lower()
    if tag == "input" and (role == "combobox" or aria in ("list", "both")):
        # each candidate value, with progressively shorter queries: async
        # autocompletes often return nothing for a full string but match a prefix
        saw_options = False
        for v in [str(x) for x in values]:
            v_n = _norm_text(v)
            queries = [v[:80]]
            if "," in v:
                queries.append(v.split(",")[0].strip())
            if " " in v:
                queries.append(v.split()[0].strip())
            seen_q = set()
            for query in [q for q in queries if not (q in seen_q or seen_q.add(q))]:
                matched, had_opts = _combobox_try(page, el, query, v_n)
                saw_options = saw_options or had_opts
                if matched:
                    return True
        if saw_options:  # options existed but none matched — never guess
            try:
                el.fill("")
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            return False
        # not option-driven at all — free-entry combobox: type + commit
        try:
            el.fill("")
            el.type(value[:80], delay=25)
            page.keyboard.press("Tab")
            page.wait_for_timeout(400)
            if el.input_value():
                return True
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        return False
    el.fill(value)
    return True


def _combobox_try(page, el, query: str, target_n: str) -> tuple[bool, bool]:
    """Type one query into a combobox; click an option matching target_n.
    Polls for async option lists. Never clicks a non-matching option.
    Returns (matched, saw_options)."""
    try:
        el.click(timeout=5000)
        el.fill("", timeout=3000)
        el.type(query, delay=25)
    except Exception:  # zero-width react-select input: force focus + type
        el.evaluate(
            "e => { const c = e.closest('.select__control') || e.parentElement;"
            " c.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));"
            " e.focus(); }")
        page.keyboard.type(query, delay=25)

    # Scope option search to THIS widget's listbox (page-wide [role=option]
    # is poisoned by the phone widget's ~250 country codes).
    def _opts():
        lb = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
        if lb and page.locator(f"[id='{lb}']").count():
            return page.locator(f"[id='{lb}'] [role='option']")
        menu = page.locator(".select__menu [role='option'], [class*='select__option']")
        if menu.count():
            return menu
        return page.locator("[role='option']:not([class*='iti'])")

    opts, n = None, 0
    for _ in range(12):  # poll up to ~6s for async lists
        page.wait_for_timeout(500)
        opts = _opts()
        n = opts.count()
        if n:
            break
    if not n:
        return False, False
    query_n = _norm_text(query)
    best = None
    for i in range(min(n, 30)):
        t_n = _norm_text(opts.nth(i).inner_text())
        if t_n == target_n:
            best = i
            break
        if best is None and (target_n in t_n or t_n in target_n
                             or (query_n and query_n in t_n)):
            best = i
    if best is None:
        return False, True
    opts.nth(best).click()
    page.wait_for_timeout(400)
    return True, True


def _upload_resume(page, resume_path: str) -> bool:
    """Upload the resume like a human: click the Attach control nearest the
    Resume label and feed the native file chooser. Falls back to setting the
    hidden input directly. Returns True only when the page shows the file."""
    name = resume_path.rsplit("/", 1)[-1]

    def _visible() -> bool:
        page.wait_for_timeout(4000)  # let the ATS parse it
        return page.locator(f"text={name}").count() > 0 or \
            page.locator("text=/resume.*\\.(pdf|docx?)/i").count() > 0

    try:
        attach = page.get_by_role("button", name=re.compile(r"attach", re.I)).first
        if attach.count() and attach.is_visible():
            with page.expect_file_chooser(timeout=8000) as fc:
                attach.click()
            fc.value.set_files(resume_path)
            if _visible():
                return True
    except Exception as exc:  # noqa: BLE001
        log.info("attach-button upload path failed: %s", exc)
    try:
        fi = page.locator("input[type='file']")
        if fi.count():
            fi.first.set_input_files(resume_path)
            return _visible()
    except Exception as exc:  # noqa: BLE001
        log.warning("direct file-input upload failed: %s", exc)
    return False


def _combobox_options(page, el) -> list[str]:
    """Open a combobox without typing and enumerate its option texts."""
    try:
        el.click(timeout=4000)
    except Exception:  # noqa: BLE001
        return []
    page.wait_for_timeout(1200)
    lb = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
    loc = None
    if lb and page.locator(f"[id='{lb}']").count():
        loc = page.locator(f"[id='{lb}'] [role='option']")
    if loc is None or loc.count() == 0:
        menu = page.locator(".select__menu [role='option']")
        if menu.count():
            loc = menu
    texts = []
    if loc is not None:
        for i in range(min(loc.count(), 30)):
            t = loc.nth(i).inner_text().strip()
            if t:
                texts.append(t)
    page.keyboard.press("Escape")
    return texts


def _click_combobox_option(page, el, text: str) -> bool:
    """Open a combobox and click the option whose text matches exactly."""
    try:
        el.click(timeout=4000)
    except Exception:  # noqa: BLE001
        return False
    page.wait_for_timeout(800)
    lb = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
    if lb and page.locator(f"[id='{lb}']").count():
        opts = page.locator(f"[id='{lb}'] [role='option']")
    else:
        opts = page.locator(".select__menu [role='option']")
    tn = _norm_text(text)
    for i in range(min(opts.count(), 30)):
        if _norm_text(opts.nth(i).inner_text()) == tn:
            opts.nth(i).click()
            page.wait_for_timeout(300)
            return True
    page.keyboard.press("Escape")
    return False


def _is_combobox(el) -> bool:
    try:
        return ((el.get_attribute("role") or "").lower() == "combobox"
                or (el.get_attribute("aria-autocomplete") or "").lower() in ("list", "both"))
    except Exception:  # noqa: BLE001
        return False


def _is_required(el, label: str) -> bool:
    if "*" in (label or ""):
        return True
    try:
        return (el.get_attribute("required") is not None
                or el.get_attribute("aria-required") == "true")
    except Exception:  # noqa: BLE001
        return False


def _fill_fields(page, profile: dict, materials: dict, result: dict,
                 posting: dict) -> tuple[int, list[str]]:
    """Fill everything we can. Returns (n_filled, reasons_for_review).

    Unknown OPTIONAL fields with no confident answer are left blank; unknown
    REQUIRED ones send the application to needs_review."""
    identity = _identity_map(profile)
    review_reasons: list[str] = []
    filled = 0
    try:
        cover_letter = materials["cover_letter"].read_text()
    except Exception:  # noqa: BLE001
        cover_letter = None
    job_context = (f"Company: {posting['company']}\nRole: {posting['title']}\n"
                   f"Job location: {posting.get('location') or 'not stated'}")

    # 1. resume upload — verified: only counts if the filename shows on page
    if _upload_resume(page, str(materials["resume_pdf"])):
        filled += 1
    elif page.locator("input[type='file']").count() > 0:
        review_reasons.append("resume upload could not be verified")

    # 2. visible text inputs / textareas / selects, matched by label text
    controls = page.locator(
        "input:visible:not([type='file']):not([type='hidden']):not([type='submit']), "
        "textarea:visible, select:visible")
    n_controls = controls.count()
    log.info("fill: %d visible controls", n_controls)
    screening_budget = 20  # max Claude-answered fields per form
    for i in range(min(n_controls, 80)):
        el = controls.nth(i)
        try:
            label = _label_for(page, el)
            log.info("fill: field %d/%d label=%r", i + 1, n_controls,
                     (label or "")[:60])
            if not label:
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            typ = (el.get_attribute("type") or "").lower()
            current = el.input_value() if tag != "select" else ""
            if current:
                continue  # ATS resume-parse already filled it

            if re.search(r"cover\s*letter", label, re.I) and tag == "textarea":
                el.fill(materials["cover_letter"].read_text())
                filled += 1
                continue
            if re.search(r"research\s+(statement|interests?)", label, re.I) and tag == "textarea":
                if materials.get("research_statement"):
                    el.fill(materials["research_statement"].read_text())
                    filled += 1
                continue

            matched = next((v for pat, v in identity if pat.search(label)), None)
            if matched is not None:
                if typ in ("checkbox", "radio"):
                    pass  # identity fields are never check/radio; skip
                elif _set_value(page, el, tag, matched):
                    filled += 1
                else:
                    # Combobox whose options don't textually contain the fact
                    # (e.g. graduation buckets like "Aug 2027 or later"):
                    # enumerate options and let Claude map the profile fact.
                    handled = False
                    cb_opts = _combobox_options(page, el) if tag == "input" else []
                    if cb_opts and screening_budget > 0:
                        screening_budget -= 1
                        qa = screening.answer_question(
                            label, options=cb_opts, job_context=job_context)
                        qa["question"], qa["options"] = label, cb_opts
                        result["screening_qa"].append(qa)
                        if (qa["confidence"] == "high"
                                and _click_combobox_option(page, el, qa["answer"])):
                            filled += 1
                            handled = True
                    if not handled and _is_required(el, label):
                        review_reasons.append(f"no matching option for {label[:60]!r}")
                continue

            # Unknown field → screening question path
            if typ in ("checkbox", "radio"):
                continue  # handled via fieldset scan below
            if screening_budget <= 0:
                review_reasons.append(f"screening budget exhausted at: {label[:60]!r}")
                continue
            screening_budget -= 1
            if tag == "select":
                options = [o.strip() for o in el.locator("option").all_inner_texts()
                           if o.strip() and not o.strip().lower().startswith("select")][:40]
                qa = screening.answer_question(label, options=options, job_context=job_context)
                qa["options"] = options
            elif _is_combobox(el):
                cb_opts = _combobox_options(page, el)
                if cb_opts:
                    qa = screening.answer_question(label, options=cb_opts, job_context=job_context)
                    qa["options"] = cb_opts
                else:
                    qa = screening.answer_question(label, cover_letter=cover_letter, job_context=job_context)
            else:
                qa = screening.answer_question(label, cover_letter=cover_letter, job_context=job_context)
            qa["question"] = label
            qa["required"] = _is_required(el, label)
            result["screening_qa"].append(qa)
            if qa["confidence"] != "high":
                if _is_required(el, label):
                    review_reasons.append(f"low confidence: {label[:80]!r}")
                else:
                    log.info("fill: leaving optional field blank: %r", label[:60])
                continue
            applied_ok = False
            if qa.get("options") and tag == "input":
                applied_ok = _click_combobox_option(page, el, qa["answer"])
            if not applied_ok:
                applied_ok = _set_value(page, el, tag, qa["answer"])
            if applied_ok:
                filled += 1
            elif _is_required(el, label):
                review_reasons.append(f"no matching option for {label[:60]!r}")
        except Exception as exc:  # noqa: BLE001
            review_reasons.append(f"field error: {exc}"[:120])

    # 3. radio/checkbox groups (Greenhouse/Ashby render these as fieldsets)
    groups = page.locator("fieldset:visible, [role='radiogroup']:visible")
    for i in range(min(groups.count(), 30)):
        g = groups.nth(i)
        try:
            legend = _first_text(g, "legend, [class*='label'], label")
            if not legend:
                continue
            options = [t.strip() for t in g.locator("label").all_inner_texts() if t.strip()]
            options = [o for o in options if o != legend]
            if not options:
                continue
            qa = screening.answer_question(legend, options=options, job_context=job_context)
            qa["question"] = legend
            qa["options"] = options
            qa["required"] = "*" in legend
            result["screening_qa"].append(qa)
            if qa["confidence"] != "high":
                if "*" in legend:
                    review_reasons.append(f"low confidence: {legend[:80]!r}")
                else:
                    log.info("fill: leaving optional group blank: %r", legend[:60])
                continue
            g.locator(f"label:has-text({qa['answer']!r})").first.click()
            filled += 1
        except Exception as exc:  # noqa: BLE001
            review_reasons.append(f"group error: {exc}"[:120])

    return filled, review_reasons


def _label_for(page, el) -> str | None:
    try:
        lid = el.get_attribute("id")
        if lid:
            lab = page.locator(f"label[for='{lid}']")
            if lab.count():
                return lab.first.inner_text().strip()
        aria = el.get_attribute("aria-label")
        if aria:
            return aria.strip()
        placeholder = el.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
        # closest label ancestor
        return el.evaluate(
            "e => { const l = e.closest('label'); return l ? l.innerText : "
            "(e.closest('[class*=field],[class*=question]')?.querySelector('label,[class*=label]')?.innerText || null); }")
    except Exception:  # noqa: BLE001
        return None


def _first_text(root, selector: str) -> str | None:
    loc = root.locator(selector)
    if loc.count():
        text = loc.first.inner_text().strip()
        return text or None
    return None
