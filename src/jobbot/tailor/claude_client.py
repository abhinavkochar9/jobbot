"""Thin Claude API wrapper with JSON-mode helper and retries."""

import json
import logging
import os
import time

import anthropic

log = logging.getLogger(__name__)

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY missing from .env")
        _client = anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)
    return _client


def _call(model: str, system: str, user: str, max_tokens: int = 4096,
          retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            resp = client().messages.create(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}])
            return "".join(b.text for b in resp.content if b.type == "text")
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            last = exc
            wait = 15 * (attempt + 1)
            log.warning("Claude API error (attempt %d): %s — retrying in %ds",
                        attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Claude API failed after {retries} attempts: {last}")


def ask_claude_text(*, model: str, system: str, user: str,
                    max_tokens: int = 2048) -> str:
    return _call(model, system, user, max_tokens)


def ask_claude_json(*, model: str, system: str, user: str,
                    max_tokens: int = 4096) -> dict:
    last_err = None
    for attempt in range(2):  # empty/malformed responses happen; one re-ask
        raw = _call(model, system, user, max_tokens)
        text = raw.strip()
        if text.startswith("```"):  # tolerate ```json fences
            text = text.split("```")[1]
            text = text[4:] if text.startswith("json") else text
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                last_err = exc
        else:
            last_err = ValueError(f"No JSON object in response: {raw[:200]}")
        log.warning("Claude JSON parse failed (attempt %d): %s", attempt + 1, last_err)
    raise ValueError(f"Claude JSON failed after retry: {last_err}")
