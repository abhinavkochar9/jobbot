# jobbot RUNBOOK

> **MODE: KITS (since 2026-09-18).** The bot discovers and scores every 2 h; each
> morning at 7 it emails one **application kit** per qualifying posting (score ≥ 65,
> max 5/day, best first): the link, attached tailored resume + letter + statement,
> and a field-by-field answer sheet scanned from the live form. you submit.
> Employers that restrict AI-assisted applications get your own CV and "own words"
> guidance instead of drafts. `auto_apply: false` — set true to resume auto-submits.
> Send kits now: `cd ~/jobbot && PYTHONPATH=src .venv/bin/python -m jobbot.kit 5`
> Tune: `kits.threshold`, `kits.per_day` in config.yaml (reloads each cycle).


Autonomous research-internship application bot. Lives at `~/jobbot` on the
umkc server (`ssh <server>`), runs as a systemd **user** service.

## Start / stop / status
```bash
ssh <server>
systemctl --user start jobbot      # start
systemctl --user stop jobbot       # stop
systemctl --user status jobbot     # status
systemctl --user restart jobbot    # restart (e.g. after config edits — config
                                   # is re-read each cycle, restart is only
                                   # needed for code changes)
```

## Logs
```bash
tail -f ~/jobbot/logs/service.log      # live scheduler log
tail -f ~/jobbot/logs/scheduler.log    # same content via app logger
ls ~/jobbot/reports/                   # digests (when SMTP not configured)
ls ~/jobbot/evidence/                  # screenshots of every fill/submission
ls ~/jobbot/materials/                 # per-application generated materials
```

## Kill switch
```bash
touch ~/jobbot/STOP    # halts ALL applying immediately (discovery continues)
rm ~/jobbot/STOP       # resume
```

## DRY_RUN → live
The bot ships with `DRY_RUN=true` in `~/jobbot/.env`: it fills forms and
screenshots them into `evidence/` but never submits. After reviewing:
```bash
ssh <server> "sed -i 's/^DRY_RUN=.*/DRY_RUN=false/' ~/jobbot/.env && systemctl --user restart jobbot"
```
Flip back to `true` any time.

## Dashboard + mobile swipe app
Served on the server's Tailscale IP, token-protected (token = `APP_TOKEN` in
`~/jobbot/.env` on the server). From any device on your tailnet:

- Swipe app (phone — add to home screen): `http://<server-tailscale-ip>:8787/app?token=<APP_TOKEN>`
- Tables view: `http://<server-tailscale-ip>:8787/?token=<APP_TOKEN>`

Swipe right = apply (uses your in-card answers on the next cycle), left = skip
forever. Card types: *needs your answers* (screening questions), *apply
manually* (CAPTCHA/portal-walled — materials are pre-generated on the server),
*maybe* (scored 45-69; right-swipe pulls it into the auto-apply queue).
Answers you give are remembered globally — the same question never comes back.

## Manual commands (on the server)
```bash
cd ~/jobbot && PYTHONPATH=src .venv/bin/python -m jobbot.main <cmd>
#   discover | report [--all] | health | tailor <posting-id ...>
#   apply [--limit N] | digest | dashboard | run
```

## Add companies to the watchlist
Edit `~/jobbot/config.yaml` → `watchlist:` — the token is the slug in the
company's public job-board URL:
- Greenhouse: `boards-api.greenhouse.io/v1/boards/<token>/jobs`
- Lever: `api.lever.co/v0/postings/<token>`
- Ashby: `api.ashbyhq.com/posting-api/job-board/<token>`
- Workday: needs `host`, `tenant`, `site` (from the careers URL)
Add `research_org: true` for research labs (gives stub postings a fair score).
No restart needed — config reloads each cycle. If a token is wrong, the
watcher-health section of the daily digest will flag it.

## Secrets (`~/jobbot/.env`, gitignored, never leaves the server)
- `ANTHROPIC_API_KEY` — required for tailoring + screening answers
- `SMTP_PASSWORD` etc. — optional; without it digests land in `reports/`
- `DRY_RUN` — see above

## Safety invariants (hard-coded)
- Max 5 submissions/day, 2 per company/week, 90-day company+title dedupe,
  3–10 min random delays between applications.
- References / transcripts / advisor-contact / salary questions and any
  low-confidence screening answer → `needs_review` queue (dashboard + digest),
  never auto-submitted.
- CAPTCHA or login wall → `blocked` list in digest; never bypassed.
- LinkedIn/Indeed are never automated.
- Every fact in generated materials traces to `profile.yaml`; update that file
  when your CV changes.
