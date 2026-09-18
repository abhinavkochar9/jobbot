"""Tailored application materials: resume, cover letter, research statement.

The master profile (profile.yaml) is the single source of truth. Tailoring
changes selection and emphasis, never facts. All generated materials are
saved under materials/<company>_<role>_<date>/ for auditing.
"""

import json
import logging
import re
from datetime import date
from pathlib import Path

from .. import config as cfg
from .claude_client import ask_claude_json, ask_claude_text
from .render import render_resume_pdf
from . import prompts

log = logging.getLogger(__name__)


def _slug(text: str, maxlen: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:maxlen]


def materials_dir(company: str, title: str) -> Path:
    d = cfg.MATERIALS_DIR / f"{_slug(company, 20)}_{_slug(title)}_{date.today().isoformat()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_materials(posting: dict, *, want_statement: bool = True) -> dict:
    """Generate resume PDF + cover letter (+ research statement) for a posting.

    Returns {"dir": path, "resume_pdf": path, "cover_letter": path,
             "research_statement": path | None, "resume_plan": path}
    """
    profile = cfg.load_profile()
    config = cfg.load_config()
    model = config.get("tailoring", {}).get("model", "claude-sonnet-5")
    outdir = materials_dir(posting["company"], posting["title"])

    # reuse today's materials if they already exist (idempotent retries)
    if (outdir / "resume.pdf").exists() and (outdir / "cover_letter.txt").exists():
        log.info("reusing existing materials in %s", outdir)
        stmt = outdir / "research_statement.txt"
        return {"dir": outdir, "resume_pdf": outdir / "resume.pdf",
                "cover_letter": outdir / "cover_letter.txt",
                "research_statement": stmt if stmt.exists() else None,
                "resume_plan": outdir / "resume_plan.json"}

    job_context = (
        f"Company: {posting['company']}\nRole: {posting['title']}\n"
        f"Location: {posting.get('location') or 'unspecified'}\n"
        f"Job description:\n{(posting.get('description') or '')[:12000]}"
    )
    profile_yaml = (cfg.ROOT / "profile.yaml").read_text()

    # 1. Resume plan: Claude SELECTS AND ORDERS profile content; it cannot
    #    introduce facts. The renderer pulls all bullet text from profile.yaml.
    plan = ask_claude_json(
        model=model,
        system=prompts.RESUME_PLAN_SYSTEM,
        user=f"{prompts.RESUME_PLAN_TASK}\n\n=== MASTER PROFILE (profile.yaml) ===\n"
             f"{profile_yaml}\n\n=== TARGET JOB ===\n{job_context}",
    )
    (outdir / "resume_plan.json").write_text(json.dumps(plan, indent=2))
    resume_pdf = outdir / "resume.pdf"
    render_resume_pdf(profile, plan, resume_pdf)

    # 2. Cover letter (<300 words, research-fit, no generic flattery)
    letter = ask_claude_text(
        model=model,
        system=prompts.COVER_LETTER_SYSTEM,
        user=f"=== MASTER PROFILE ===\n{profile_yaml}\n\n=== TARGET JOB ===\n"
             f"{job_context}\n\nWrite the cover letter now.",
    )
    (outdir / "cover_letter.txt").write_text(letter.strip() + "\n")

    # 3. Research statement (150-400 words), generated once per job so it can
    #    reference the team's actual area.
    statement_path = None
    if want_statement:
        statement = ask_claude_text(
            model=model,
            system=prompts.RESEARCH_STATEMENT_SYSTEM,
            user=f"=== MASTER PROFILE ===\n{profile_yaml}\n\n=== TARGET JOB ===\n"
                 f"{job_context}\n\nWrite the research statement now.",
        )
        statement_path = outdir / "research_statement.txt"
        statement_path.write_text(statement.strip() + "\n")

    (outdir / "posting.json").write_text(json.dumps(
        {k: posting.get(k) for k in ("company", "title", "url", "location", "score")},
        indent=2))
    log.info("materials generated in %s", outdir)
    return {"dir": outdir, "resume_pdf": resume_pdf,
            "cover_letter": outdir / "cover_letter.txt",
            "research_statement": statement_path,
            "resume_plan": outdir / "resume_plan.json"}
