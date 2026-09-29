"""CLI: python -m collector {setup,sync,leaders}

Environment:
  APIFY_TOKEN            Apify API token
  APIFY_ACTOR_ID         Optional; default automation-lab/tracxn-company-intelligence-scraper
  AIRTABLE_PAT           Airtable personal access token (same secret as kylas-airtable-sync)
                         (scopes: data.records:read/write, schema.bases:read/write)
  AIRTABLE_BASE_ID       Existing base to write to (appXXXX...)
  AIRTABLE_WORKSPACE_ID  Only for `setup` when creating a brand-new base
  START_DATE             First round date to keep (default: Jan 1 of this year)
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import airtable, apify, leaders
from .normalize import normalize, parse_date
from .schema import FUNDING_TABLE, LEADERS_TABLE, FundingRecord

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "funding_rounds.json"
REPORTS = ROOT / "reports"


def _env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"missing environment variable {name}")
    return value


def _airtable_token() -> str:
    return _env("AIRTABLE_PAT", required=False) or _env("AIRTABLE_TOKEN")


def _start_date(arg) -> date:
    raw = arg or os.environ.get("START_DATE")
    return parse_date(raw) if raw else date(date.today().year, 1, 1)


def _load_snapshot() -> list:
    if not SNAPSHOT.exists():
        sys.exit(f"no snapshot at {SNAPSHOT}; run `python -m collector sync` first")
    rows = json.loads(SNAPSHOT.read_text())
    return [FundingRecord(**{**r, "round_date": parse_date(r["round_date"])}) for r in rows]


def cmd_setup(args):
    token = _airtable_token()
    base_id = _env("AIRTABLE_BASE_ID", required=False)
    if base_id:
        created = airtable.ensure_tables(token, base_id)
        print(f"base {base_id}: created tables {created or 'none (already present)'}")
    else:
        base_id = airtable.create_base(token, _env("AIRTABLE_WORKSPACE_ID"), args.name)
        print(f"created base {base_id}; set AIRTABLE_BASE_ID={base_id}")


def cmd_sync(args):
    since = _start_date(args.since)
    today = date.today()
    if args.from_file:
        items = json.loads(Path(args.from_file).read_text())
    else:
        urls = apify.load_urls(args.urls)
        if not urls:
            sys.exit(f"no Tracxn company URLs in {args.urls}")
        print(f"scraping {len(urls)} companies from {args.urls}")
        actor = _env("APIFY_ACTOR_ID", required=False) or apify.DEFAULT_ACTOR
        items = apify.run_actor(_env("APIFY_TOKEN"), actor, apify.build_input(urls))
    print(f"fetched {len(items)} raw items")

    # Merge with the previous snapshot so the research table accumulates over time.
    merged = {r.key: r for r in (_load_snapshot() if SNAPSHOT.exists() else [])}
    fresh = normalize(items, since=since)
    for rec in fresh:
        merged[rec.key] = rec
    skipped = len(items) - len(fresh)
    if skipped:
        print(f"{skipped} items skipped (failed scrape, or no funding round since {since})")
    records = sorted(merged.values(), key=lambda r: (r.round_date or date.min), reverse=True)

    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps([r.to_dict() for r in records], indent=2) + "\n")
    print(f"{len(records)} rounds since {since} saved to {SNAPSHOT.relative_to(ROOT)}")

    leader_rows = _write_reports(records, today, args.top)

    if args.no_airtable:
        return
    token, base_id = _airtable_token(), _env("AIRTABLE_BASE_ID")
    n = airtable.upsert(token, base_id, FUNDING_TABLE, [r.to_airtable(today) for r in records])
    print(f"upserted {n} rows into '{FUNDING_TABLE}'")
    n = airtable.upsert(token, base_id, LEADERS_TABLE, leader_rows)
    print(f"upserted {n} rows into '{LEADERS_TABLE}'")


def _write_reports(records, today, top) -> list:
    REPORTS.mkdir(exist_ok=True)
    airtable_rows = []
    for period in ("last-month", "last-quarter"):
        label, start, end = leaders.period_bounds(period, today)
        rows = leaders.rank(records, start, end, top)
        (REPORTS / f"leaders_{label}.md").write_text(leaders.to_markdown(rows, label))
        airtable_rows += leaders.to_airtable(rows, label)
        print(f"report: reports/leaders_{label}.md ({len(rows)} companies)")
    return airtable_rows


def cmd_leaders(args):
    records = _load_snapshot()
    today = parse_date(args.as_of) if args.as_of else date.today()
    label, start, end = leaders.period_bounds(args.period, today)
    print(leaders.to_markdown(leaders.rank(records, start, end, args.top), label))


def main(argv=None):
    p = argparse.ArgumentParser(prog="collector", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="create the Airtable base / tables")
    s.add_argument("--name", default="Tracxn Research")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("sync", help="scrape via Apify, update snapshot, reports and Airtable")
    s.add_argument("--since", help="YYYY-MM-DD; default START_DATE or Jan 1")
    s.add_argument("--urls", default=str(ROOT / "companies.txt"),
                   help="file of Tracxn company-profile URLs, one per line")
    s.add_argument("--from-file", help="use a saved Apify dataset JSON instead of running the actor")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--no-airtable", action="store_true")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("leaders", help="print top-funded companies from the snapshot")
    s.add_argument("--period", choices=["last-month", "last-quarter"], default="last-month")
    s.add_argument("--as-of", help="YYYY-MM-DD; default today")
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(func=cmd_leaders)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
