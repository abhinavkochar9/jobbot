"""jobbot dashboard + mobile swipe app.

Served on the server's Tailscale IP (tailnet-only) with token auth.
  Phone:  http://<server-tailscale-ip>:8787/app?token=<APP_TOKEN>   (add to home screen)
  Laptop: same URL, or ssh -L 8787:127.0.0.1:8787 <server> if bound to localhost.

Swipe right → apply (queues the job; if it had open questions, your in-app
answers feed the next fill). Swipe left → skip forever. Blocked jobs show the
posting link + ready-made materials for a manual apply.
"""

import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config as cfg
from . import db

app = FastAPI(title="jobbot", docs_url=None, redoc_url=None)


def _check_token(request: Request) -> None:
    expected = os.environ.get("APP_TOKEN", "")
    supplied = (request.headers.get("x-app-token")
                or request.query_params.get("token", ""))
    if not expected or supplied != expected:
        raise HTTPException(status_code=401, detail="bad token")


def _rows(query: str, args: tuple = ()) -> list[dict]:
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


# ---------------- read APIs (dashboard tables) ----------------

@app.get("/api/postings")
def postings(request: Request):
    _check_token(request)
    return _rows("SELECT id, company, title, url, location, score, eligible, "
                 "ineligible_reason, first_seen FROM postings ORDER BY score DESC LIMIT 500")


@app.get("/api/applications")
def applications(request: Request):
    _check_token(request)
    return _rows("SELECT a.*, p.company, p.title, p.url FROM applications a "
                 "JOIN postings p ON p.id=a.posting_id ORDER BY a.timestamp DESC LIMIT 500")


@app.get("/api/health")
def health(request: Request):
    _check_token(request)
    return _rows("SELECT * FROM watcher_health ORDER BY watcher")


# ---------------- swipe queue ----------------

@app.get("/api/queue")
def queue(request: Request):
    _check_token(request)
    cards = []
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT p.*,
              (SELECT status FROM applications a WHERE a.posting_id=p.id
               ORDER BY a.timestamp DESC LIMIT 1) AS app_status,
              (SELECT decision FROM swipes s WHERE s.posting_id=p.id) AS decision
            FROM postings p WHERE p.eligible=1 AND p.source NOT LIKE 'agg:%'
            ORDER BY p.score DESC""").fetchall()
        for r in rows:
            if r["decision"] in ("apply", "skip") and r["app_status"] != "needs_review":
                continue
            kind = None
            if r["app_status"] == "needs_review":
                kind = "questions"
            elif r["app_status"] in ("blocked", "failed"):
                kind = "manual"
            elif r["app_status"] is None and r["score"] < 70 and r["score"] >= 45:
                kind = "maybe"
            if not kind:
                continue
            questions = [dict(q) for q in conn.execute(
                "SELECT id, question, options_json, answer FROM pending_questions "
                "WHERE posting_id=? AND answer IS NULL", (r["id"],))]
            materials = conn.execute(
                "SELECT materials_path FROM applications WHERE posting_id=? "
                "AND materials_path IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
                (r["id"],)).fetchone()
            cards.append({
                "posting_id": r["id"], "company": r["company"], "title": r["title"],
                "url": r["url"], "location": r["location"], "score": r["score"],
                "kind": kind, "questions": questions,
                "materials": materials["materials_path"] if materials else None,
                "description": (r["description"] or "")[:600],
            })
    return cards


@app.post("/api/swipe")
async def swipe(request: Request):
    _check_token(request)
    body = await request.json()
    pid, decision = int(body["posting_id"]), body["decision"]
    if decision not in ("apply", "skip"):
        raise HTTPException(400, "decision must be apply|skip")
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO swipes (posting_id, decision, timestamp) VALUES (?,?,?) "
            "ON CONFLICT(posting_id) DO UPDATE SET decision=excluded.decision, "
            "timestamp=excluded.timestamp", (pid, decision, db.now_iso()))
    return {"ok": True}


@app.post("/api/answer")
async def answer(request: Request):
    _check_token(request)
    body = await request.json()
    qid, text = int(body["question_id"]), str(body["answer"]).strip()
    if not text:
        raise HTTPException(400, "empty answer")
    with db.get_db() as conn:
        row = conn.execute("SELECT question FROM pending_questions WHERE id=?",
                           (qid,)).fetchone()
        if not row:
            raise HTTPException(404, "question not found")
        conn.execute("UPDATE pending_questions SET answer=?, answered_at=? WHERE id=?",
                     (text, db.now_iso(), qid))
        # remember globally so the same question never comes back on another job
        db.set_user_answer(conn, row["question"], text)
    return {"ok": True}


# ---------------- pages ----------------

APP_PAGE = """<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>jobbot</title><style>
*{box-sizing:border-box;margin:0;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;
     height:100dvh;overflow:hidden;display:flex;flex-direction:column}
header{padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
h1{font-size:18px}#count{color:#888;font-size:13px}
#stack{flex:1;position:relative;margin:0 12px 12px}
.card{position:absolute;inset:0;background:#1c1c1e;border-radius:20px;padding:20px;
      display:flex;flex-direction:column;gap:10px;transition:transform .25s,opacity .25s;
      touch-action:pan-y;overflow-y:auto}
.badge{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#0af}
.badge.manual{color:#fa0}.badge.maybe{color:#8f8}
.score{position:absolute;top:18px;right:20px;font-size:26px;font-weight:700;color:#0af}
h2{font-size:22px;line-height:1.2}.co{color:#aaa;font-size:15px}
.desc{color:#999;font-size:13px;line-height:1.45}
.q{background:#2a2a2d;border-radius:12px;padding:10px}
.q label{font-size:13px;color:#ccc;display:block;margin-bottom:6px}
.q input,.q select,.q textarea{width:100%;background:#111;color:#eee;border:1px solid #444;
  border-radius:8px;padding:8px;font-size:14px}
.hint{text-align:center;color:#666;font-size:12px;padding:4px}
.btns{display:flex;gap:12px;padding:0 16px 20px}
button{flex:1;padding:16px;border:0;border-radius:14px;font-size:16px;font-weight:600}
#no{background:#3a2225;color:#f66}#yes{background:#1e3a26;color:#6f6}
a{color:#0af;text-decoration:none;font-size:14px}
.done{display:flex;height:100%;align-items:center;justify-content:center;color:#666}
</style></head><body>
<header><h1>jobbot</h1><span id="count"></span></header>
<div id="stack"></div>
<div class="hint">&larr; skip &nbsp;&middot;&nbsp; apply &rarr;</div>
<div class="btns"><button id="no">Skip</button><button id="yes">Apply</button></div>
<script>
const token = new URLSearchParams(location.search).get('token') || localStorage.token;
if (token) localStorage.token = token;
const H = {'x-app-token': token, 'Content-Type': 'application/json'};
let cards = [], idx = 0;

async function load(){
  cards = await (await fetch('/api/queue', {headers: H})).json();
  idx = 0; render();
}
function render(){
  const stack = document.getElementById('stack');
  document.getElementById('count').textContent = (cards.length - idx) + ' in queue';
  if (idx >= cards.length){
    stack.innerHTML = '<div class="done">All caught up 🎉</div>'; return;
  }
  const c = cards[idx];
  const kinds = {questions: 'needs your answers', manual: 'apply manually (CAPTCHA/portal)', maybe: 'maybe — your call'};
  stack.innerHTML = `<div class="card" id="card">
    <span class="badge ${c.kind}">${kinds[c.kind]}</span>
    <span class="score">${c.score}</span>
    <h2>${c.title}</h2><div class="co">${c.company} · ${c.location || ''}</div>
    <a href="${c.url}" target="_blank">open posting ↗</a>
    ${c.kind === 'manual' && c.materials ? '<div class="q">Materials ready on server:<br><small>' + c.materials + '</small></div>' : ''}
    ${c.questions.map(q => {
      const opts = q.options_json ? JSON.parse(q.options_json) : null;
      return `<div class="q"><label>${q.question}</label>` + (opts
        ? `<select data-q="${q.id}"><option value="">— choose —</option>` +
          opts.map(o => `<option>${o}</option>`).join('') + '</select>'
        : `<textarea data-q="${q.id}" rows="2" placeholder="your answer"></textarea>`) + '</div>';
    }).join('')}
    <div class="desc">${c.description}</div></div>`;
  attachSwipe(document.getElementById('card'));
}
async function decide(decision){
  const c = cards[idx];
  if (decision === 'apply'){
    for (const el of document.querySelectorAll('[data-q]')){
      if (el.value) await fetch('/api/answer', {method:'POST', headers:H,
        body: JSON.stringify({question_id: el.dataset.q, answer: el.value})});
    }
  }
  await fetch('/api/swipe', {method:'POST', headers:H,
    body: JSON.stringify({posting_id: c.posting_id, decision})});
  const card = document.getElementById('card');
  card.style.transform = `translateX(${decision==='apply'?'':'-'}120%) rotate(${decision==='apply'?'':'-'}12deg)`;
  card.style.opacity = 0;
  setTimeout(() => { idx++; render(); }, 220);
}
function attachSwipe(card){
  let x0 = null;
  card.addEventListener('touchstart', e => x0 = e.touches[0].clientX, {passive:true});
  card.addEventListener('touchmove', e => {
    if (x0 === null) return;
    const dx = e.touches[0].clientX - x0;
    card.style.transform = `translateX(${dx}px) rotate(${dx/24}deg)`;
  }, {passive:true});
  card.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - x0; x0 = null;
    if (dx > 90) decide('apply'); else if (dx < -90) decide('skip');
    else card.style.transform = '';
  });
}
document.getElementById('yes').onclick = () => decide('apply');
document.getElementById('no').onclick = () => decide('skip');
load();
</script></body></html>"""


@app.get("/app", response_class=HTMLResponse)
def swipe_app(request: Request):
    _check_token(request)
    return APP_PAGE


PAGE = """<!DOCTYPE html><html><head><title>jobbot</title><style>
body{font-family:system-ui;margin:20px;background:#fafafa}
h2{margin:24px 0 8px}table{border-collapse:collapse;width:100%;background:#fff}
td,th{border:1px solid #ddd;padding:4px 8px;font-size:13px;text-align:left}
tr:nth-child(even){background:#f4f4f4}.err{color:#b00}
a{color:#06c;text-decoration:none}</style></head><body>
<h1>jobbot</h1><p><a href="#" id="applink">open swipe app</a></p>
<h2>Applications</h2><table id="apps"></table>
<h2>Top postings</h2><table id="posts"></table>
<h2>Watcher health</h2><table id="health"></table>
<script>
const token = new URLSearchParams(location.search).get('token') || localStorage.token;
if (token) localStorage.token = token;
document.getElementById('applink').href = '/app?token=' + token;
const H = {'x-app-token': token};
async function load(id, url, cols){
  const data = await (await fetch(url, {headers: H})).json();
  const t = document.getElementById(id);
  t.innerHTML = '<tr>' + cols.map(c=>'<th>'+c+'</th>').join('') + '</tr>' +
    data.slice(0,80).map(r=>'<tr>'+cols.map(c=>{
      let v = r[c]==null?'':r[c];
      if(c=='url') v = '<a href="'+v+'" target=_blank>link</a>';
      if(c=='last_error'&&v) v='<span class=err>'+String(v).slice(0,60)+'</span>';
      return '<td>'+v+'</td>';}).join('')+'</tr>').join('');
}
load('apps','/api/applications',['timestamp','status','company','title','notes','url']);
load('posts','/api/postings',['score','company','title','eligible','first_seen','url']);
load('health','/api/health',['watcher','postings_found','last_run','last_error']);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    _check_token(request)
    return PAGE


def serve() -> None:
    import uvicorn
    config = cfg.load_config().get("dashboard", {})
    host = os.environ.get("DASHBOARD_HOST") or config.get("host", "127.0.0.1")
    uvicorn.run(app, host=host, port=config.get("port", 8787), log_level="warning")
