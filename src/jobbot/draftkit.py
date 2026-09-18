"""Draft kits for Anthropic applications.

Anthropic's applicant policy (anthropic.com/candidate-ai-guidance) prohibits
using AI to *generate* application content: "Please create your first draft
yourself, then use Claude to refine it." The Fellows form requires an explicit
attestation that you have read it.

So this module writes NO answers. It assembles a workspace:
  - the real application URL and program facts
  - the actual form questions, verbatim
  - YOUR OWN material, quoted from profile.yaml, grouped by which question it
    is relevant to, so the first draft is yours to write from your own record.
"""

import logging
import textwrap
from datetime import date
from pathlib import Path

from . import config as cfg

log = logging.getLogger(__name__)

FELLOWS_FACTS = """\
PROGRAM FACTS (from the posting + application form, fetched {today})
  Real application:  https://bit.ly/afpsafety
                     -> airtable.com/appCHLjgoTUCJMLct/pagUhpiBE5KxoU3lX/form
                     Run by Constellation, Anthropic's recruiting partner.
                     Updates arrive from a Constellation address.
  STATUS AS OF FETCH: "Applications are closed for the November cohort,
                     submissions will be stored until review for the next
                     cohort starts."  (Greenhouse posting says the next cohort
                     is expected to start January 2027; the form says the
                     upcoming cohort "will likely start in October". Verify
                     before submitting.)
  Duration:          4 months, full-time
  Location:          shared workspace in Berkeley, CA or London, UK
  Compensation:      weekly stipend
  Conversion:        25-50% of fellows in previous cohorts received a
                     full-time Anthropic offer
  NOTE: The Greenhouse listing is NOT the application. Its only required
        field is an acknowledgement that you must apply via Constellation.

WORK AUTHORIZATION — RESOLVE THIS FIRST
  The form states: "We are not currently able to sponsor visas for fellows.
  To participate you need to have or independently obtain full-time work
  authorization in one of: USA, UK, Canada," and you must be based in that
  country for the program's duration.
  You are F-1 with CPT. Whether a 4-month full-time fellowship at Anthropic
  qualifies as CPT is a question for your UMKC DSO / international student
  office — CPT must be integral to your curriculum and authorized by the
  school. Confirm this BEFORE writing the essays; if the answer is no, the
  application is moot.

TEAM STREAMS (pick a top choice + any additional)
  - AI Safety & Alignment
  - AI Safety: Mechanistic Interpretability & Model Internals
  - AI Security & Frontier Red Team
  - ML Systems & Performance
  - Reinforcement Learning
  - Economics & Policy
"""

QUESTIONS = [
    ("Why are you interested in participating in the Fellows program?",
     "1-2 paragraphs. REQUIRED.",
     ["Your own reasons. What in their published research pulled you in?",
      "Reading list to draw on: alignment.anthropic.com (Recommendations for",
      "Technical AI Safety Research Directions; Subliminal Learning), and",
      "anthropic.com/research/open-source-circuit-tracing."]),
    ("With your selected team(s) in mind, tell us about one or more research "
     "areas you're excited about right now, and why.",
     "1 paragraph. REQUIRED.",
     ["Your own current work is the natural material here — see YOUR RECORD."]),
    ("(Optional) Relevant background, with links: research experience, "
     "coursework, self-directed study, past roles, relevant projects.",
     "1 paragraph. Optional but you have real material for it.",
     ["See YOUR RECORD below; include the arXiv links."]),
    ("How likely are you to accept a full-time offer at Anthropic if you "
     "receive one after the program?",
     "Brief explanation + a % estimate. REQUIRED.",
     ["Your own honest estimate. They say you needn't be confident in it."]),
    ("How likely are you to continue working in your selected streams after "
     "the program?",
     "Brief explanation + a % estimate. REQUIRED.",
     ["Your own honest estimate."]),
    ("Earliest full-time start date after the program",
     "REQUIRED. Note your PhD timeline (expected Dec 2029).", []),
    ("Berkeley or London workspace for the program's duration?",
     "REQUIRED.", []),
]

REFERENCES_BLOCK = """\
REFERENCES — THREE REQUIRED, and this needs lead time.
  For each: Name, Email, their background (title, website, Scholar), and
  context on your relationship (what you worked on, when, how long, how
  closely).
  "References are one of the main ways we assess candidates. The most useful
   references come from people who've collaborated with you on technical work."
  ML-research-community references preferred.
  !! "We plan to reach out to your references without giving you notice."
     So ask them BEFORE you submit.

  Candidates from your record (your call, not mine):
    - Dr. Yugyung Lee — PhD advisor, UMKC; co-author on both 2026 preprints
    - Prof. Michael Farmer — UMKC Bloch School; first author on both preprints
    - Dr. Mei Fu — NIH-funded lymphedema lab, GRA Jul 2024-Dec 2025
"""


def _your_record(profile: dict) -> str:
    """Quote the candidate's OWN material, verbatim from profile.yaml."""
    out = ["YOUR RECORD — raw material, quoted from your CV. Draw on it in your",
           "own words; do not paste it.", ""]
    out.append("PREPRINTS")
    for p in profile.get("publications", []):
        out.append(f"  - {p['authors']}. \"{p['title']}\" {p['venue']}, {p['year']}.")
    out.append("")
    out.append("CURRENT RESEARCH (Jan 2026-present, Dr. Lee's lab)")
    for e in profile.get("experience", []):
        if "Lee" in e.get("role", ""):
            for b in e.get("bullets", []):
                out.append("  - " + textwrap.fill(b, 76, subsequent_indent="    "))
    out.append("")
    out.append("PROJECTS most relevant to interpretability / model internals")
    for pr in profile.get("projects", []):
        tags = pr.get("tags", [])
        if any(t in tags for t in ("world-models", "robot-learning", "llm", "rag",
                                   "imitation-learning")):
            out.append(f"  {pr['name']} ({pr['dates']})")
            for b in pr.get("bullets", [])[:3]:
                out.append("    - " + textwrap.fill(b, 74, subsequent_indent="      "))
    out.append("")
    out.append("LINKS THE FORM ASKS FOR")
    c = profile["contact"]
    out.append(f"  LinkedIn (required): {c['linkedin']}")
    out.append(f"  GitHub:              {c['github']}")
    out.append("  Google Scholar:      (add your profile URL — not in the CV)")
    return "\n".join(out)


def build_fellows_kit(outdir: Path | None = None) -> Path:
    profile = cfg.load_profile()
    outdir = outdir or (cfg.MATERIALS_DIR / f"anthropic_fellows_{date.today().isoformat()}")
    outdir.mkdir(parents=True, exist_ok=True)

    parts = [
        "=" * 78,
        "ANTHROPIC FELLOWS PROGRAM — DRAFT KIT",
        "You write the answers. This file is your source material and a map of",
        "the form; it deliberately contains no drafted responses, because",
        "Anthropic's applicant policy requires your first draft to be yours.",
        "Once you have a draft, ask Claude to help you sharpen it — that part",
        "they explicitly allow.",
        "=" * 78,
        "",
        FELLOWS_FACTS.format(today=date.today().isoformat()),
        "",
        "-" * 78,
        "FORM QUESTIONS — write your drafts under each",
        "-" * 78,
        "",
    ]
    for i, (q, meta, notes) in enumerate(QUESTIONS, 1):
        parts.append(f"[{i}] {textwrap.fill(q, 74, subsequent_indent='    ')}")
        parts.append(f"    ({meta})")
        for n in notes:
            parts.append(f"    | {n}")
        parts.append("")
        parts.append("    YOUR DRAFT:")
        parts.append("    " + "_" * 68)
        parts.append("")
        parts.append("")
    parts.append("-" * 78)
    parts.append(REFERENCES_BLOCK)
    parts.append("-" * 78)
    parts.append(_your_record(profile))

    path = outdir / "FELLOWS_DRAFT_KIT.txt"
    path.write_text("\n".join(parts) + "\n")
    log.info("draft kit written to %s", path)
    return path


if __name__ == "__main__":
    cfg.setup_logging()
    print(build_fellows_kit())
