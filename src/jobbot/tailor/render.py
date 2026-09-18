"""Render a resume plan + profile into a single-column PDF.

All bullet/fact text comes verbatim from profile.yaml — the plan only selects
and orders. The only generated strings are the objective line and summary,
which the plan constrains to profile facts.
"""

import logging
from pathlib import Path

from jinja2 import Template

log = logging.getLogger(__name__)

RESUME_TEMPLATE = Template(r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  @page { size: Letter; margin: 14mm 15mm; }
  * { box-sizing: border-box; }
  body { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 9.6pt;
         color: #1a1a1a; line-height: 1.32; margin: 0; }
  h1 { font-size: 17pt; letter-spacing: 2.5px; margin: 0 0 2px; }
  .contact { font-size: 8.8pt; color: #333; margin-bottom: 8px; }
  h2 { font-size: 10.2pt; letter-spacing: 1.2px; text-transform: uppercase;
       border-bottom: 1px solid #444; padding-bottom: 1px; margin: 10px 0 4px; }
  .row { display: flex; justify-content: space-between; }
  .org { font-weight: 600; }
  .role { font-style: italic; font-size: 9.2pt; }
  .dates { color: #444; font-size: 8.8pt; white-space: nowrap; }
  ul { margin: 2px 0 6px; padding-left: 14px; }
  li { margin-bottom: 1.5px; }
  .summary { margin: 2px 0 0; }
  .skills p { margin: 1.5px 0; }
  .pub { margin-bottom: 3px; }
  a { color: #1a1a1a; text-decoration: none; }
</style></head><body>

<h1>{{ p.contact.name | upper }}</h1>
<div class="contact">
  {{ p.contact.location }} &middot; {{ p.contact.phone }} &middot; {{ p.contact.email }}<br>
  {{ p.contact.linkedin | replace("https://","") }} &middot; {{ p.contact.github | replace("https://","") }} &middot; Google Scholar
</div>

{% if plan.summary %}<h2>Summary</h2><p class="summary">{{ plan.summary }}</p>{% endif %}

<h2>Education</h2>
{% for e in p.education %}
<div class="row"><span><span class="org">{{ e.institution }}</span> — {{ e.degree }}{% if e.gpa %}, GPA {{ e.gpa }}{% endif %}{% if e.cgpa %}, CGPA {{ e.cgpa }}{% endif %}{% if e.advisor %} (Advisor: {{ e.advisor }}){% endif %}</span>
<span class="dates">{{ e.dates }}</span></div>
{% endfor %}

{% if plan.lead_publications %}
<h2>Preprints</h2>
{% for pub in p.publications %}
<div class="pub">{{ pub.authors }}. &ldquo;{{ pub.title }}.&rdquo; <i>{{ pub.venue }}</i>, {{ pub.year }}.</div>
{% endfor %}
{% endif %}

<h2>Research &amp; Work Experience</h2>
{% for idx in plan.experience_order %}{% set e = p.experience[idx] %}
<div class="row"><span><span class="org">{{ e.organization }}</span> — <span class="role">{{ e.role }}</span></span>
<span class="dates">{{ e.dates }}</span></div>
<ul>{% for bi in plan.experience_bullets.get(idx|string, range(e.bullets|length)|list) %}<li>{{ e.bullets[bi] }}</li>{% endfor %}</ul>
{% endfor %}

<h2>Selected Projects</h2>
{% for idx in plan.project_order %}{% set pr = p.projects[idx] %}
<div class="row"><span><span class="org">{{ pr.name }}</span>{% if pr.label %} — <span class="role">{{ pr.label }}</span>{% endif %}</span>
<span class="dates">{{ pr.dates }}</span></div>
<ul>{% for bi in plan.project_bullets.get(idx|string, range(pr.bullets|length)|list) %}<li>{{ pr.bullets[bi] }}</li>{% endfor %}</ul>
{% endfor %}

{% if not plan.lead_publications %}
<h2>Preprints</h2>
{% for pub in p.publications %}
<div class="pub">{{ pub.authors }}. &ldquo;{{ pub.title }}.&rdquo; <i>{{ pub.venue }}</i>, {{ pub.year }}.</div>
{% endfor %}
{% endif %}

<h2>Technical Skills</h2>
<div class="skills">
{% for key in plan.skills_sections %}{% if p.skills.get(key) %}
<p><b>{{ key | replace("_", " ") | title }}:</b> {{ p.skills[key] | join(", ") }}</p>
{% endif %}{% endfor %}
</div>

{% if plan.include_leadership %}
<h2>Leadership &amp; Awards</h2>
<ul>
{% for l in p.leadership %}<li><b>{{ l.role }}</b>, {{ l.organization }} ({{ l.dates }})</li>{% endfor %}
{% for a in p.awards %}<li>{{ a }}</li>{% endfor %}
</ul>
{% endif %}

</body></html>""")


def render_resume_html(profile: dict, plan: dict) -> str:
    # Defensive: clamp all indices to valid ranges so a bad plan can't crash
    n_exp, n_proj = len(profile["experience"]), len(profile["projects"])
    plan = dict(plan)
    plan["experience_order"] = [i for i in plan.get("experience_order", range(n_exp)) if 0 <= i < n_exp]
    plan["project_order"] = [i for i in plan.get("project_order", range(n_proj)) if 0 <= i < n_proj][:4]
    plan.setdefault("experience_bullets", {})
    plan.setdefault("project_bullets", {})
    for key, items, coll in (("experience_bullets", plan["experience_order"], "experience"),
                             ("project_bullets", plan["project_order"], "projects")):
        cleaned = {}
        for idx in items:
            bullets = profile[coll][idx]["bullets"]
            keep = plan[key].get(str(idx), list(range(len(bullets))))
            cleaned[str(idx)] = [b for b in keep if 0 <= b < len(bullets)] or list(range(len(bullets)))
        plan[key] = cleaned
    plan.setdefault("skills_sections", list(profile["skills"].keys())[:6])
    plan.setdefault("lead_publications", True)
    plan.setdefault("include_leadership", True)
    return RESUME_TEMPLATE.render(p=profile, plan=plan)


def render_resume_pdf(profile: dict, plan: dict, out_pdf: Path) -> None:
    html = render_resume_html(profile, plan)
    html_path = out_pdf.with_suffix(".html")
    html_path.write_text(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.pdf(path=str(out_pdf), format="Letter",
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                 print_background=True)
        browser.close()
    log.info("rendered %s", out_pdf)
