"""Title matching: which postings are candidate research internships."""

import re

INCLUDE_PATTERNS = [
    r"research\s+intern",                      # research intern(ship)
    r"research\s+scien\w*[\s\w,–-]*intern",    # research scientist/science internship
    r"ph\.?\s?d\.?\s+(research\s+)?intern",
    r"student\s+researcher",
    r"applied\s+scien(?:ce|tist)\s+intern",
    r"(?:machine\s+learning|deep\s+learning|\bml\b|\bai\b|robotics)[\s\w]*intern",
    r"intern[\s,–-]+(?:research|machine\s+learning|\bai\b|robotics)",
    # research-area keyword anywhere in an intern title ("X Intern – World Models")
    r"(?:world\s+models?|imitation\s+learning|vision[\s-]?language|\bvla\b|\bllms?\b|"
    r"language\s+models?|computer\s+vision|reinforcement\s+learning|perception|"
    r"autonomous\s+driving|interpretability|\bagents?\b|frontier|"
    r"foundation\s+models?)[\s\w,–-]*intern",
    r"intern[\s\w,–-]*(?:world\s+models?|imitation\s+learning|vision[\s-]?language|"
    r"\bvla\b|\bllms?\b|language\s+models?|computer\s+vision|reinforcement\s+learning|"
    r"perception|interpretability)",
]
_INCLUDE = [re.compile(p, re.IGNORECASE) for p in INCLUDE_PATTERNS]

# Non-"intern" research titles (Fellows Program, Research Engineer/Scientist,
# Residency). Only applied for companies in config `broad_title_companies`
# (Anthropic), so the bot never auto-applies to senior full-time roles at
# companies where it should be internship-only.
BROAD_PATTERNS = [
    r"fellows?\s+program",
    r"\bresiden(?:t|cy)\b",
    r"research\s+(?:engineer|scientist)",
]
_BROAD = [re.compile(p, re.IGNORECASE) for p in BROAD_PATTERNS]
SENIORITY_RE = re.compile(
    r"\b(senior|staff|principal|lead|director|head\s+of|manager|vp)\b", re.IGNORECASE)

# Titles that match INCLUDE but are not research roles
EXCLUDE_TITLE = [
    re.compile(p, re.IGNORECASE) for p in [
        r"software\s+(dev|eng)\w*\s+intern",   # pure SWE (research-org check in scoring)
        r"\bit\s+intern\b",
        r"(marketing|sales|recruit\w*|finance|legal|graphic\s+design|\bhr\b)\s+intern",
        r"undergrad",
        r"high\s+school",
        r"hardware|mechanical|electrical|manufacturing|process\s+intern",
    ]
]

# Description red flags → not eligible regardless of score
CITIZENSHIP_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"u\.?s\.?\s+citizen(ship)?\s+(is\s+)?required",
        r"must\s+be\s+a\s+u\.?s\.?\s+citizen",
        r"(security|government)\s+clearance\s+(is\s+)?required",
        r"active\s+(ts|top\s+secret|secret)\s+clearance",
        r"itar\s+requirements?",
        r"only\s+u\.?s\.?\s+citizens",
    ]
]

UNDERGRAD_ONLY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"currently\s+pursuing\s+(a|an)\s+bachelor(?!.{0,80}(master|phd|graduate))",
        r"undergraduate\s+students?\s+only",
        r"rising\s+(junior|senior)",
    ]
]


def title_matches(title: str, *, broad: bool = False) -> bool:
    if any(p.search(title) for p in EXCLUDE_TITLE):
        return False
    if any(p.search(title) for p in _INCLUDE):
        return True
    if broad and not SENIORITY_RE.search(title):
        return any(p.search(title) for p in _BROAD)
    return False


def requires_citizenship_or_clearance(text: str) -> bool:
    return any(p.search(text or "") for p in CITIZENSHIP_PATTERNS)


def undergrad_only(text: str) -> bool:
    return any(p.search(text or "") for p in UNDERGRAD_ONLY_PATTERNS)


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    import html
    return _TAG_RE.sub(" ", html.unescape(text or "")).strip()

# Employers that prohibit AI assistance in applications (Anthropic, Waymo, and
# a growing number of others). If a posting or its form says this, we must NOT
# auto-fill or auto-submit it — the form usually also asks the candidate to
# ATTEST to the policy, so an automated submission would be a false attestation.
# Detected -> the application is routed to the human as a manual card.
AI_POLICY_PATTERNS = [
    r"prohibits?\s+the\s+use\s+of\s+unauthorized\s+outside\s+assistance",
    r"unauthorized\s+(outside\s+)?assistance[^.]{0,80}(artificial\s+intelligence|\bAI\b)",
    r"(without|not)\s+(the\s+)?(use\s+of\s+)?(AI|artificial\s+intelligence)[^.]{0,60}(assist|tool|generat)",
    r"do\s+not\s+use\s+(AI|artificial\s+intelligence|generative)",
    r"(AI|artificial\s+intelligence)\s+tools?[^.]{0,60}(are\s+)?(not\s+permitted|prohibited|not\s+allowed)",
    r"complete\s+this\s+application[^.]{0,60}(without|unaided|on\s+your\s+own)",
    r"your\s+own\s+(words|work)[^.]{0,40}(without|no)\s+(AI|assistance)",
    r"policy\s+for\s+using\s+AI\s+in\s+(our|the)\s+application",
]
_AI_POLICY = [re.compile(p, re.IGNORECASE) for p in AI_POLICY_PATTERNS]


def prohibits_ai_assistance(text: str) -> str | None:
    """Return the matched policy phrase if the employer restricts AI use in
    applications, else None."""
    for pat in _AI_POLICY:
        m = pat.search(text or "")
        if m:
            return m.group(0)[:160]
    return None
