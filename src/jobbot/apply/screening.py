"""Screening-question answering via Claude, profile.yaml as sole ground truth."""

import logging
import re

from .. import config as cfg
from ..tailor.claude_client import ask_claude_json
from ..tailor.prompts import SCREENING_SYSTEM

log = logging.getLogger(__name__)

# Questions that ALWAYS go to human review, regardless of Claude confidence.
ALWAYS_REVIEW = [
    re.compile(p, re.IGNORECASE) for p in [
        r"referen[cs]e", r"transcript", r"advisor|adviser|supervisor",
        r"recommend(er|ation)", r"salary|compensation|pay\s+expectation",
    ]
]


def needs_human(question: str) -> bool:
    return any(p.search(question) for p in ALWAYS_REVIEW)


_cache: dict[tuple, dict] = {}  # (question, options) → answer; screening
# questions repeat heavily across ATS forms, so cache within the process

# Free-text questions that the tailored cover letter legitimately answers
COVER_LETTER_QUESTIONS = re.compile(
    r"why\s+(do\s+you\s+)?(want|join|us|" r"\w+\?)|good\s+fit|motivat|"
    r"additional\s+information|anything\s+else|cover\s+letter|"
    r"tell\s+us\s+(more|about)|why\s+are\s+you\s+interested", re.IGNORECASE)


def answer_question(question: str, *, options: list[str] | None = None,
                    model: str | None = None,
                    cover_letter: str | None = None,
                    job_context: str | None = None) -> dict:
    """Returns {"answer": str, "confidence": "high"|"low", "reason": str}."""
    if needs_human(question):
        return {"answer": "", "confidence": "low",
                "reason": "references/transcript/advisor/salary — always human review"}

    # answers the user provided via the swipe app / chat take absolute priority
    from .. import db as _db
    with _db.get_db() as conn:
        stored = _db.get_user_answer(conn, question)
    if stored is not None:
        if options and stored not in options:
            best = next((o for o in options if stored.lower() in o.lower()
                         or o.lower() in stored.lower()), None)
            if best is None:
                return {"answer": stored, "confidence": "low",
                        "reason": "stored answer doesn't match offered options"}
            stored = best
        return {"answer": stored, "confidence": "high", "reason": "user-provided answer"}

    # free-text "why us / anything else" → the tailored cover letter, which is
    # already constrained to profile facts
    if cover_letter and not options and COVER_LETTER_QUESTIONS.search(question):
        return {"answer": cover_letter.strip(), "confidence": "high",
                "reason": "answered with tailored cover letter"}

    key = (question.strip().lower(), tuple(options or ()))
    if key in _cache:
        return dict(_cache[key])

    config = cfg.load_config()
    model = model or config.get("tailoring", {}).get("model", "claude-sonnet-5")
    profile_yaml = (cfg.ROOT / "profile.yaml").read_text()
    prompt = f"=== MASTER PROFILE ===\n{profile_yaml}\n"
    if job_context:
        prompt += f"\n=== JOB CONTEXT ===\n{job_context}\n"
    prompt += f"\n=== SCREENING QUESTION ===\n{question}"
    if options:
        prompt += ("\n\nThis is a choice field. You MUST pick exactly one of these "
                   f"options verbatim as the answer: {options}")
    result = ask_claude_json(model=model, system=SCREENING_SYSTEM, user=prompt,
                             max_tokens=1024)
    if options and result.get("answer") not in options:
        # Claude failed to map onto an offered option — never guess.
        result["confidence"] = "low"
        result["reason"] = f"answer not among offered options: {result.get('answer')!r}"
    for key in ("answer", "confidence", "reason"):
        result.setdefault(key, "")
    if result["confidence"] not in ("high", "low"):
        result["confidence"] = "low"
    _cache[key] = dict(result)
    return result
