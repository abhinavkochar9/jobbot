"""Watchers for big careers portals with public search JSON endpoints.

These are best-effort: portals change their endpoints without notice.
Failures surface in watcher health + the daily digest (manual_watch fallback).
"""

import logging

from .base import get_json

log = logging.getLogger(__name__)


def microsoft(query: str) -> list[dict]:
    data = get_json(
        "https://gcsservices.careers.microsoft.com/search/api/v1/search",
        params={"q": query, "l": "en_us", "pg": 1, "pgSz": 50,
                "o": "Relevance", "flt": "true"})
    result = (data.get("operationResult") or {}).get("result") or {}
    jobs = []
    for j in result.get("jobs", []):
        props = j.get("properties") or {}
        jobs.append({
            "company": "Microsoft",
            "title": j.get("title", ""),
            "url": f"https://jobs.careers.microsoft.com/global/en/job/{j.get('jobId')}",
            "location": (props.get("locations") or [None])[0],
            "description": props.get("description", ""),
        })
    return jobs


def google(query: str) -> list[dict]:
    jobs = []
    for page in (1, 2):
        data = get_json(
            "https://careers.google.com/api/v3/search/",
            params={"q": query, "page": page, "employment_type": "INTERN"})
        for j in data.get("jobs", []):
            job_id = (j.get("id") or "").split("/")[-1]
            jobs.append({
                "company": "Google",
                "title": j.get("title", ""),
                "url": j.get("apply_url")
                       or f"https://www.google.com/about/careers/applications/jobs/results/{job_id}",
                "location": ", ".join(l.get("display", "") for l in (j.get("locations") or [])[:2]),
                "description": " ".join(filter(None, [
                    j.get("description"), j.get("qualifications"),
                    j.get("responsibilities")])),
            })
        if not data.get("jobs"):
            break
    return jobs


def amazon(query: str) -> list[dict]:
    data = get_json(
        "https://www.amazon.jobs/en/search.json",
        params={"base_query": query, "result_limit": 50, "offset": 0})
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": "Amazon (Science/AGI)",
            "title": j.get("title", ""),
            "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
            "location": j.get("normalized_location"),
            "description": " ".join(filter(None, [
                j.get("description"), j.get("basic_qualifications"),
                j.get("preferred_qualifications")])),
        })
    return jobs
