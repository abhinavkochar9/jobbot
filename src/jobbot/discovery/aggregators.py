"""Aggregator watchers: Remotive, HN Who is hiring, Adzuna."""

import logging
import os

from .base import get_json

log = logging.getLogger(__name__)


def remotive() -> list[dict]:
    data = get_json("https://remotive.com/api/remote-jobs",
                    params={"search": "research intern"})
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": j.get("company_name", "?"),
            "title": j.get("title", ""),
            "url": j.get("url", ""),
            "location": j.get("candidate_required_location"),
            "description": j.get("description", ""),
        })
    return jobs


def hn_whoishiring() -> list[dict]:
    """Search 'Ask HN: Who is hiring?' comments for research internships,
    limited to the last 60 days (older threads are dead leads)."""
    import time
    cutoff = int(time.time()) - 60 * 24 * 3600
    data = get_json(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"query": '"research intern"', "tags": "comment",
                "hitsPerPage": 50,
                "numericFilters": f"created_at_i>{cutoff}"})
    jobs = []
    for hit in data.get("hits", []):
        story = hit.get("story_title") or ""
        if "who is hiring" not in story.lower():
            continue
        text = hit.get("comment_text") or ""
        first_line = text.split("<p>")[0][:120] or "HN posting"
        jobs.append({
            "company": f"HN: {first_line}",
            "title": "research intern (HN Who is hiring)",
            "url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "location": None,
            "description": text,
        })
    return jobs


def adzuna() -> list[dict]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("Adzuna enabled but ADZUNA_APP_ID/KEY missing in .env")
    data = get_json(
        "https://api.adzuna.com/v1/api/jobs/us/search/1",
        params={"app_id": app_id, "app_key": app_key,
                "what": "research intern machine learning", "results_per_page": 50})
    jobs = []
    for j in data.get("results", []):
        jobs.append({
            "company": (j.get("company") or {}).get("display_name", "?"),
            "title": j.get("title", ""),
            "url": j.get("redirect_url", ""),
            "location": (j.get("location") or {}).get("display_name"),
            "description": j.get("description", ""),
        })
    return jobs
