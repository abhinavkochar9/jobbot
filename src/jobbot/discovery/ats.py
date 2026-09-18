"""Watchers for public ATS job-board APIs.

Each function takes a watchlist entry and returns a list of dicts:
  {company, title, url, location, description}
Descriptions may be HTML; the caller strips tags.
"""

import logging

from .. import matching
from .base import get_json, post_json

log = logging.getLogger(__name__)


def greenhouse(entry: dict) -> list[dict]:
    token, company = entry["token"], entry["company"]
    data = get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        params={"content": "true"})
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": company,
            "title": j.get("title", ""),
            "url": j.get("absolute_url", ""),
            "location": (j.get("location") or {}).get("name"),
            "description": j.get("content", ""),
        })
    return jobs


def lever(entry: dict) -> list[dict]:
    token, company = entry["token"], entry["company"]
    data = get_json(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        jobs.append({
            "company": company,
            "title": j.get("text", ""),
            "url": j.get("hostedUrl", ""),
            "location": cats.get("location"),
            "description": j.get("descriptionPlain") or j.get("description", ""),
        })
    return jobs


def ashby(entry: dict) -> list[dict]:
    token, company = entry["token"], entry["company"]
    data = get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        params={"includeCompensation": "false"})
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": company,
            "title": j.get("title", ""),
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "location": j.get("location"),
            "description": j.get("descriptionPlain") or j.get("descriptionHtml", ""),
        })
    return jobs


def smartrecruiters(entry: dict) -> list[dict]:
    token, company = entry["token"], entry["company"]
    jobs, offset = [], 0
    while True:
        data = get_json(
            f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
            params={"limit": 100, "offset": offset})
        content = data.get("content", [])
        if not content:
            break
        for j in content:
            title = j.get("name", "")
            # Fetch full description only for title-matched roles (polite rates)
            description = ""
            if matching.title_matches(title):
                try:
                    detail = get_json(
                        f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{j['id']}")
                    sections = (detail.get("jobAd") or {}).get("sections") or {}
                    description = " ".join(
                        s.get("text", "") for s in sections.values() if isinstance(s, dict))
                except Exception as exc:  # noqa: BLE001
                    log.warning("smartrecruiters detail fetch failed for %s: %s", title, exc)
            loc = j.get("location") or {}
            jobs.append({
                "company": company,
                "title": title,
                "url": f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                "location": ", ".join(filter(None, [loc.get("city"), loc.get("country")])),
                "description": description,
            })
        offset += len(content)
        if offset >= data.get("totalFound", 0) or offset >= 1000:
            break
    return jobs


def workable(entry: dict) -> list[dict]:
    token, company = entry["token"], entry["company"]
    data = get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{token}",
        params={"details": "true"})
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": company,
            "title": j.get("title", ""),
            "url": j.get("url") or j.get("shortlink", ""),
            "location": ", ".join(filter(None, [j.get("city"), j.get("country")])),
            "description": j.get("description", ""),
        })
    return jobs


def eightfold(entry: dict) -> list[dict]:
    """Eightfold-powered careers sites (e.g. Netflix)."""
    company = entry["company"]
    data = get_json(
        f"https://{entry['host']}/api/apply/v2/jobs",
        params={"domain": entry["domain"], "query": entry.get("query", "research intern"),
                "num": 50, "start": 0})
    jobs = []
    for j in data.get("positions", []):
        jobs.append({
            "company": company,
            "title": j.get("name", ""),
            "url": j.get("canonicalPositionUrl")
                   or f"https://{entry['host']}/careers/job/{j.get('id')}",
            "location": j.get("location"),
            "description": j.get("job_description", ""),
        })
    return jobs


def workday(entry: dict) -> list[dict]:
    """Generic Workday CXS watcher. Fetches descriptions only for matched titles."""
    company = entry["company"]
    base = f"https://{entry['host']}/wday/cxs/{entry['tenant']}/{entry['site']}"
    jobs, offset = [], 0
    while offset < 200:  # safety cap
        data = post_json(f"{base}/jobs", json_body={
            "appliedFacets": {}, "limit": 20, "offset": offset,
            "searchText": "research intern"})
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            title = j.get("title", "")
            path = j.get("externalPath", "")
            description = ""
            if matching.title_matches(title) and path:
                try:
                    detail = get_json(f"{base}{path}")
                    description = (detail.get("jobPostingInfo") or {}).get("jobDescription", "")
                except Exception as exc:  # noqa: BLE001
                    log.warning("workday detail fetch failed for %s: %s", title, exc)
            jobs.append({
                "company": company,
                "title": title,
                "url": f"https://{entry['host']}/en-US/{entry['site']}{path}",
                "location": j.get("locationsText"),
                "description": description,
            })
        offset += len(postings)
        if offset >= data.get("total", 0):
            break
    return jobs
