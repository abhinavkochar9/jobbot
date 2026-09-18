"""jobbot CLI.

  python -m jobbot.main discover           # run one discovery cycle
  python -m jobbot.main report [--all]     # show eligible postings by score
  python -m jobbot.main health             # watcher health
  python -m jobbot.main tailor <id> [...]  # generate materials for posting ids
  python -m jobbot.main apply [--limit N]  # process application queue (respects DRY_RUN)
  python -m jobbot.main digest             # compose+send the daily digest now
  python -m jobbot.main dashboard          # serve read-only dashboard
  python -m jobbot.main run                # 24/7 scheduler loop
"""

import argparse
import json
import sys

from . import config as cfg
from . import db
from .discovery import run_discovery


def cmd_discover() -> None:
    config = cfg.load_config()
    summary = run_discovery(config)
    print(f"\nWatchers ok: {summary['watchers_ok']}, "
          f"failed: {len(summary['watchers_failed'])}, "
          f"title-matched postings: {summary['total_matched']}, "
          f"new: {len(summary['new'])}")
    for name, err in summary["watchers_failed"]:
        print(f"  FAILED {name}: {err}")


def cmd_report(show_all: bool = False) -> None:
    config = cfg.load_config()
    threshold = config.get("score_threshold", 70)
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM postings WHERE eligible=1 ORDER BY score DESC").fetchall()
    print(f"{'score':>5}  {'company':<28} {'title':<58} url")
    for r in rows:
        if not show_all and r["score"] < threshold:
            continue
        print(f"{r['score']:>5}  {r['company'][:28]:<28} {r['title'][:58]:<58} {r['url']}")
    if show_all:
        below = [r for r in rows if r["score"] < threshold]
        print(f"\n({len(below)} below threshold {threshold} shown above too)")


def cmd_health() -> None:
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM watcher_health ORDER BY watcher").fetchall()
    for r in rows:
        status = "OK " if r["last_error"] is None else "ERR"
        print(f"{status} {r['watcher']:<38} found={r['postings_found']:<4} "
              f"last_run={r['last_run']} err={r['last_error'] or '-'}")


def cmd_tailor(posting_ids: list[int]) -> None:
    from .tailor import generate_materials
    with db.get_db() as conn:
        for pid in posting_ids:
            row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
            if not row:
                print(f"posting {pid} not found")
                continue
            out = generate_materials(dict(row))
            print(f"[{pid}] {row['company']} — {row['title']}\n  -> {out['dir']}")


def cmd_apply(limit: int | None) -> None:
    from .apply import process_queue
    results = process_queue(cfg.load_config(), limit=limit)
    for r in results:
        print(f"{r['status']:<15} {r['company']} — {r['title']}"
              f"{('  [' + r['notes'] + ']') if r.get('notes') else ''}")
    if not results:
        print("nothing to do (caps reached, STOP present, or no eligible postings)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobbot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    rp = sub.add_parser("report")
    rp.add_argument("--all", action="store_true")
    sub.add_parser("health")
    tp = sub.add_parser("tailor")
    tp.add_argument("ids", nargs="+", type=int)
    ap = sub.add_parser("apply")
    ap.add_argument("--limit", type=int, default=None)
    sub.add_parser("digest")
    sub.add_parser("dashboard")
    sub.add_parser("run")
    args = parser.parse_args()

    cfg.setup_logging()
    if args.command == "discover":
        cmd_discover()
    elif args.command == "report":
        cmd_report(show_all=args.all)
    elif args.command == "health":
        cmd_health()
    elif args.command == "tailor":
        cmd_tailor(args.ids)
    elif args.command == "apply":
        cmd_apply(args.limit)
    elif args.command == "digest":
        from . import notify
        print(notify.daily_digest(cfg.load_config())[:2000])
    elif args.command == "dashboard":
        from .dashboard import serve
        serve()
    elif args.command == "run":
        from .scheduler import run_forever
        run_forever()


if __name__ == "__main__":
    main()
