"""Score postings 0-100 against the candidate's research profile.

Components:
  research-area overlap  0-50  (world models / robot learning / LLM interp /
                                theoretical ML weighted highest)
  PhD-intern eligibility 0-20
  authorization          0-15  (citizenship/clearance → ineligible)
  timing fit             0-15
"""

import json
import re

from . import matching

# (regex, points) — capped at 50. Priority areas weighted highest.
RESEARCH_KEYWORDS = [
    (r"world\s+model", 14),
    (r"imitation\s+learning|robot\s+learning|learning\s+from\s+demonstration", 14),
    (r"interpretab|mechanistic|belief\s+revision", 12),
    (r"information\s+geometry|learning\s+dynamics|optimization\s+theory|learning\s+theory|gradient\s+flow", 12),
    (r"large\s+language\s+model|\bllms?\b|foundation\s+model", 10),
    (r"vision[\s-]language|\bvlm\b|multimodal", 9),
    (r"robot|manipulation|locomotion|embodied", 8),
    (r"reinforcement\s+learning|\brl\b|policy\s+learning", 7),
    (r"agentic|ai\s+agent|retrieval[\s-]augmented|\brag\b", 6),
    (r"computer\s+vision|perception|pose\s+estimation", 5),
    (r"deep\s+learning|neural\s+network|machine\s+learning", 4),
    (r"quantum\s+(machine\s+learning|computing)", 3),
    (r"diffusion|generative\s+model|video\s+generation", 5),
    (r"biomechanic|healthcare|clinical|medical\s+imaging", 4),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), pts) for p, pts in RESEARCH_KEYWORDS]


def configure(config: dict) -> None:
    """Optional overrides from config.yaml → `scoring:` block:
      research_keywords: [[regex, points], ...]   (replaces the default list)
      preferred_locations: regex                   (replaces the Bay-Area default)
    Called once per discovery cycle; defaults stay if the block is absent."""
    global _COMPILED, PREFERRED_LOCATION_RE
    sc = (config or {}).get("scoring") or {}
    if sc.get("research_keywords"):
        _COMPILED = [(re.compile(p, re.IGNORECASE), int(pts)) for p, pts in sc["research_keywords"]]
    if sc.get("preferred_locations"):
        PREFERRED_LOCATION_RE = re.compile(sc["preferred_locations"], re.IGNORECASE)

# F-1 CPT authorizes US employment only. Non-US postings stay visible but are
# penalized and flagged — a Canadian/European internship needs a separate permit.
NON_US_LOCATION_RE = re.compile(
    r"\b(canada|toronto|montreal|vancouver|china|shanghai|beijing|shenzhen|"
    r"india|bangalore|bengaluru|hyderabad|uk|united kingdom|london|germany|"
    r"berlin|munich|france|paris|japan|tokyo|korea|seoul|singapore|israel|"
    r"tel aviv|mexico|guadalajara|bulgaria|sofia|poland|warsaw|ireland|dublin|"
    r"netherlands|amsterdam|switzerland|zurich|sweden|stockholm|australia|"
    r"sydney|taiwan|taipei|brazil|s[aã]o paulo)\b", re.IGNORECASE)
US_HINT_RE = re.compile(
    r"\b(united states|usa|u\.s\.|remote[\s-]*us|us[\s-]*remote|"
    r"[a-z ]+,\s*(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|"
    r"ma|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|"
    r"vt|va|wa|wv|wi|wy)\b)", re.IGNORECASE)


def non_us_location(location: str | None) -> bool:
    if not location:
        return False
    return bool(NON_US_LOCATION_RE.search(location)) and not US_HINT_RE.search(location)


# Locations that get a queue-priority boost (config-driven; see PREFERRED_LOCATION_RE)
PREFERRED_LOCATION_RE = re.compile(
    r"san\s+francisco|\bsf\b|bay\s+area|palo\s+alto|mountain\s+view|"
    r"menlo\s+park|sunnyvale|south\s+san\s+francisco|redwood\s+city|"
    r"santa\s+clara|san\s+jose|berkeley|oakland", re.IGNORECASE)

PHD_RE = re.compile(r"ph\.?\s?d|doctoral|doctorate", re.IGNORECASE)
MS_PHD_RE = re.compile(r"(ms|m\.s\.|master)['’s]*\s*(/|or|and)\s*ph\.?\s?d", re.IGNORECASE)
GRAD_RE = re.compile(r"graduate\s+(student|program|degree)|enrolled\s+in\s+a\s+(master|phd)", re.IGNORECASE)


def score_posting(title: str, description: str, target_terms: list[str],
                  research_org: bool = False,
                  location: str | None = None) -> tuple[int, dict, bool, str | None]:
    """Returns (score, breakdown, eligible, ineligible_reason)."""
    text = f"{title}\n{description or ''}"
    thin = len((description or "").strip()) < 200

    # Hard disqualifiers
    if matching.requires_citizenship_or_clearance(text):
        return 0, {"disqualified": "citizenship/clearance required"}, False, "citizenship/clearance"
    if matching.undergrad_only(text):
        return 0, {"disqualified": "undergrad-only posting"}, False, "undergrad-only"

    research = 0
    hits = []
    for pattern, pts in _COMPILED:
        if pattern.search(text):
            research += pts
            hits.append(pattern.pattern[:30])
    research = min(research, 50)
    # A research lab posting a stub description is still a research role;
    # don't let a thin board entry bury it. Flagged for manual verification.
    if thin and research_org:
        research = max(research, 30)

    if PHD_RE.search(text):
        eligibility = 20
    elif MS_PHD_RE.search(text) or GRAD_RE.search(text):
        eligibility = 15
    else:
        eligibility = 8  # unclear; not fatal

    outside_us = non_us_location(location)
    authorization = 5 if outside_us else 15  # CPT is US-only

    timing = 6  # default: internship with no term stated
    lowered = text.lower()
    for term in target_terms:
        # "Summer 2027" matches "summer 2027" or "summer intern ... 2027"
        season, _, year = term.lower().partition(" ")
        if term.lower() in lowered or (season in lowered and year in lowered):
            timing = 15
            break
    else:
        if any(y in lowered for y in ("2027", "2026")):
            timing = 10

    # Preferred-location boost (override via config: scoring.preferred_locations)
    location_bonus = 0
    if location and PREFERRED_LOCATION_RE.search(location):
        location_bonus = 6

    total = min(100, research + eligibility + authorization + timing + location_bonus)
    breakdown = {
        "research_overlap": research,
        "phd_eligibility": eligibility,
        "authorization": authorization,
        "timing_fit": timing,
        "keyword_hits": hits[:10],
    }
    if location_bonus:
        breakdown["location_bonus"] = location_bonus
    if thin:
        breakdown["thin_description"] = True
    if outside_us:
        breakdown["non_us_location"] = location
    return total, breakdown, True, None


def breakdown_json(breakdown: dict) -> str:
    return json.dumps(breakdown)
