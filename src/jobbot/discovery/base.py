"""Polite HTTP helpers shared by all watchers."""

import logging
import time

import requests

log = logging.getLogger(__name__)

def _contact() -> str:
    import os
    try:
        from ..config import load_profile
        return os.environ.get("JOBBOT_CONTACT") or load_profile()["contact"]["email"]
    except Exception:  # noqa: BLE001
        return "unknown"


USER_AGENT = f"jobbot/0.1 (personal research-internship search agent; contact: {_contact()})"
TIMEOUT = 30
_last_request_at = 0.0
MIN_INTERVAL = 1.5  # seconds between any two outbound requests


def _throttle() -> None:
    global _last_request_at
    wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
             retries: int = 2):
    return _request("GET", url, params=params, headers=headers, retries=retries)


def post_json(url: str, *, json_body: dict, headers: dict | None = None,
              retries: int = 2):
    return _request("POST", url, json_body=json_body, headers=headers, retries=retries)


def _request(method: str, url: str, *, params=None, json_body=None, headers=None,
             retries: int = 2):
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_exc = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            resp = requests.request(method, url, params=params, json=json_body,
                                    headers=hdrs, timeout=TIMEOUT)
            if resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{method} {url} failed after {retries + 1} attempts: {last_exc}")
