"""Discovery: poll ATS APIs, portals, and aggregators for matching postings.

Every watcher is isolated — one broken watcher never stops the others.
"""

import logging

from .. import db, matching, scoring
from . import aggregators, ats, portals

log = logging.getLogger(__name__)


def all_watchers(config: dict):
    """Yield (watcher_name, fetch_callable, research_org) triples. Each callable
    returns a list of dicts: {company, title, url, location, description}."""
    wl = config.get("watchlist", {})
    for entry in wl.get("greenhouse", []):
        yield f"greenhouse:{entry['token']}", (lambda e=entry: ats.greenhouse(e)), entry.get("research_org", False)
    for entry in wl.get("lever", []):
        yield f"lever:{entry['token']}", (lambda e=entry: ats.lever(e)), entry.get("research_org", False)
    for entry in wl.get("ashby", []):
        yield f"ashby:{entry['token']}", (lambda e=entry: ats.ashby(e)), entry.get("research_org", False)
    for entry in wl.get("smartrecruiters", []):
        yield f"smartrecruiters:{entry['token']}", (lambda e=entry: ats.smartrecruiters(e)), entry.get("research_org", False)
    for entry in wl.get("workable", []):
        yield f"workable:{entry['token']}", (lambda e=entry: ats.workable(e)), entry.get("research_org", False)
    for entry in wl.get("workday", []):
        yield f"workday:{entry['tenant']}", (lambda e=entry: ats.workday(e)), entry.get("research_org", False)
    for entry in wl.get("eightfold", []):
        yield f"eightfold:{entry['domain']}", (lambda e=entry: ats.eightfold(e)), entry.get("research_org", False)

    p = config.get("portals", {})
    if p.get("microsoft", {}).get("enabled"):
        yield "portal:microsoft", (lambda: portals.microsoft(p["microsoft"].get("query", "research intern"))), False
    if p.get("google", {}).get("enabled"):
        yield "portal:google", (lambda: portals.google(p["google"].get("query", "research intern"))), False
    if p.get("amazon", {}).get("enabled"):
        yield "portal:amazon", (lambda: portals.amazon(p["amazon"].get("query", "research intern"))), False

    agg = config.get("aggregators", {})
    if agg.get("remotive", {}).get("enabled"):
        yield "agg:remotive", aggregators.remotive, False
    if agg.get("hn_whoishiring", {}).get("enabled"):
        yield "agg:hn_whoishiring", aggregators.hn_whoishiring, False
    if agg.get("adzuna", {}).get("enabled"):
        yield "agg:adzuna", aggregators.adzuna, False


def run_discovery(config: dict) -> dict:
    """Run all watchers once. Returns summary: new postings, health, errors."""
    target_terms = config.get("target_terms", [])
    scoring.configure(config)
    broad_cos = {c.lower() for c in config.get("broad_title_companies", [])}
    summary = {"new": [], "watchers_ok": 0, "watchers_failed": [], "total_matched": 0}

    with db.get_db() as conn:
        for name, fetch, research_org in all_watchers(config):
            try:
                raw = fetch()
            except Exception as exc:  # noqa: BLE001 — watcher isolation by design
                log.warning("watcher %s failed: %s", name, exc)
                db.record_health(conn, name, ok=False, error=str(exc)[:300])
                summary["watchers_failed"].append((name, str(exc)[:120]))
                continue

            matched = 0
            for job in raw:
                title = (job.get("title") or "").strip()
                if not title or not matching.title_matches(
                        title, broad=job["company"].lower() in broad_cos):
                    continue
                matched += 1
                desc = matching.strip_html(job.get("description") or "")
                score, breakdown, eligible, reason = scoring.score_posting(
                    title, desc, target_terms, research_org=research_org,
                    location=job.get("location"))
                pid, is_new = db.upsert_posting(
                    conn, source=name, company=job["company"], title=title,
                    url=job["url"], location=job.get("location"),
                    description=desc[:20000], score=score,
                    breakdown=scoring.breakdown_json(breakdown),
                    eligible=eligible, ineligible_reason=reason)
                if is_new:
                    summary["new"].append({
                        "id": pid, "company": job["company"], "title": title,
                        "url": job["url"], "score": score, "eligible": eligible})
            db.record_health(conn, name, ok=True, found=matched)
            summary["watchers_ok"] += 1
            summary["total_matched"] += matched
            log.info("watcher %s: %d raw, %d matched", name, len(raw), matched)

    return summary
