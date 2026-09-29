"""CLI: python -m collector {setup,sync,leaders}

Deals come from the Apify actors listed in sources.json (funding-rounds database,
Inc42/YourStory and Entrackr news). Kept: Series A-or-later rounds, M&A and IPOs.
Each company's LinkedIn page is found via its website or a Google search.

Environment:
  APIFY_TOKEN            Apify API token
  AIRTABLE_PAT           Airtable personal access token (same secret as kylas-airtable-sync)
                         (scopes: data.records:read/write, schema.bases:read/write)
  AIRTABLE_BASE_ID       Existing base to write to (appXXXX...)
  AIRTABLE_FUNDING_TABLE Table name or ID for funding rounds (default "Funding Rounds")
  AIRTABLE_LEADERS_TABLE Table name or ID for the leaderboard (default "Monthly Leaders")
  AIRTABLE_WORKSPACE_ID  Only for `setup` when creating a brand-new base
  START_DATE             First deal date to keep (default: start of this Indian FY, 1 Apr)
  COUNTRY                Keep deals in this country when a source reports one (default India)
  MIN_UNLABELED_USD      Keep rounds with no stage label if at least this big (default 10000000)
  INR_PER_USD            Rate for amounts reported in rupees (default 88)
  LINKEDIN_LOOKUP        "off" to skip finding LinkedIn pages (default on)
  GOOGLE_SEARCH_ACTOR    Apify actor for the LinkedIn search (default apify/google-search-scraper)
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import airtable, apify, leaders, linkedin, sources
from .normalize import normalize, parse_date, still_valid
from .schema import (FUNDING_FIELDS, FUNDING_TABLE, IPO, LEADERS_FIELDS, LEADERS_TABLE, MNA,
                     FundingRecord)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "funding_rounds.json"
LINKEDIN_CACHE = ROOT / "data" / "linkedin.json"
REPORTS = ROOT / "reports"


def _env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"missing environment variable {name}")
    return value


def _airtable_token() -> str:
    raw = _env("AIRTABLE_PAT", required=False) or _env("AIRTABLE_TOKEN")
    token = raw.strip().strip("'\"")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    # Describe the token's shape (never its value) so a bad paste is easy to spot.
    print(f"Airtable token: {len(token)} chars, starts with {token[:3]!r}, "
          f"{'contains' if any(c.isspace() for c in token) else 'no'} whitespace"
          f"{', had quotes/Bearer stripped' if token != raw.strip() else ''}")
    if not token.startswith("pat"):
        print("WARNING: Airtable personal access tokens start with 'pat' "
              "(legacy 'key...' API keys no longer work)")
    return token


def _tables() -> tuple:
    return (_env("AIRTABLE_FUNDING_TABLE", required=False) or FUNDING_TABLE,
            _env("AIRTABLE_LEADERS_TABLE", required=False) or LEADERS_TABLE)


def _start_date(arg) -> date:
    raw = arg or os.environ.get("START_DATE")
    return parse_date(raw) if raw else leaders.fy_start(date.today())


def _load_snapshot() -> list:
    if not SNAPSHOT.exists():
        sys.exit(f"no snapshot at {SNAPSHOT}; run `python -m collector sync` first")
    rows = json.loads(SNAPSHOT.read_text())
    return [FundingRecord(**{**r, "round_date": parse_date(r["round_date"]),
                             "post_date": parse_date(r.get("post_date"))}) for r in rows]


def cmd_setup(args):
    token = _airtable_token()
    base_id = _env("AIRTABLE_BASE_ID", required=False)
    if base_id:
        tables = airtable.get_tables(token, base_id)
        funding, leaders_ref = _tables()
        for ref, fields in ((funding, FUNDING_FIELDS), (leaders_ref, LEADERS_FIELDS)):
            print(airtable.ensure_table(token, base_id, tables, ref, fields))
    else:
        base_id = airtable.create_base(token, _env("AIRTABLE_WORKSPACE_ID"), args.name)
        print(f"created base {base_id}; set AIRTABLE_BASE_ID={base_id}")


def cmd_sync(args):
    since = _start_date(args.since)
    today = date.today()
    if args.from_file:
        items = json.loads(Path(args.from_file).read_text())
    else:
        items = sources.run_all(_env("APIFY_TOKEN"), sources.load(Path(args.sources)),
                                ROOT, since, today, raw_dir=SNAPSHOT.parent / "raw")
    print(f"fetched {len(items)} raw items")

    min_unlabeled = None if args.all_stages else float(
        os.environ.get("MIN_UNLABELED_USD") or 10_000_000)
    fresh = normalize(items, since=since, until=today,
                      country=os.environ.get("COUNTRY", "India"),
                      min_unlabeled_usd=min_unlabeled)
    print(f"{len(fresh)} qualifying deals since {since} "
          f"({len(items) - len(fresh)} items were duplicates, off-period, not Series A+ / M&A,"
          f" or not deals)")

    # Merge with the previous snapshot so the research table accumulates over time;
    # a new report of a known deal fills in fields the earlier one lacked.
    saved = _load_snapshot() if SNAPSHOT.exists() else []
    if min_unlabeled is not None:
        saved = [r for r in saved if still_valid(r, min_unlabeled)]
    merged = {r.key: r for r in saved}
    for rec in fresh:
        merged[rec.key] = rec.merge(merged[rec.key]) if rec.key in merged else rec
    records = sorted(merged.values(), key=lambda r: (r.round_date or date.min), reverse=True)

    if os.environ.get("LINKEDIN_LOOKUP", "on").lower() not in ("off", "0", "false", "no"):
        cache = linkedin.load_cache(LINKEDIN_CACHE)
        actor = os.environ.get("GOOGLE_SEARCH_ACTOR") or linkedin.GOOGLE_ACTOR
        stats = linkedin.enrich(
            records, cache, token=_env("APIFY_TOKEN", required=False), today=today,
            search=lambda token, names: linkedin.google_search(token, names, actor))
        linkedin.save_cache(LINKEDIN_CACHE, cache)
        have = sum(bool(r.linkedin_url) for r in records)
        print(f"LinkedIn: {have}/{len(records)} deals have a page (new: {stats['website']} "
              f"from websites, {stats['google']} from Google; {stats['missed']} not found)")

    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps([r.to_dict() for r in records], indent=2) + "\n")
    n_mna = sum(r.deal_type == MNA for r in records)
    n_ipo = sum(r.deal_type == IPO for r in records)
    print(f"{len(records)} deals ({len(records) - n_mna - n_ipo} funding, {n_mna} M&A, "
          f"{n_ipo} IPO) saved to {SNAPSHOT.relative_to(ROOT)}")

    leader_rows = _write_reports(records, today, args.top)

    if args.no_airtable:
        return
    token, base_id = _airtable_token(), _env("AIRTABLE_BASE_ID")
    tables = airtable.get_tables(token, base_id)
    funding, leaders_ref = _tables()
    missing = [ref for ref in (funding, leaders_ref) if airtable.find_table(tables, ref) is None]
    if missing:
        have = ", ".join(f"'{t['name']}' ({t['id']})" for t in tables) or "none"
        sys.exit(f"Airtable table(s) {missing} not found in base {base_id}. Tables there: "
                 f"{have}. Run the 'Setup Airtable Schema' workflow to create them, or point "
                 f"AIRTABLE_FUNDING_TABLE / AIRTABLE_LEADERS_TABLE at an existing table.")
    for ref, rows in ((funding, [r.to_airtable(today) for r in records]),
                      (leaders_ref, leader_rows)):
        n = airtable.upsert(token, base_id, ref, airtable.fill_primary(tables, ref, rows))
        # Remove rows this tool wrote before that are no longer in the data
        # (rows added by hand have no Record Key and are left alone).
        # Skipped on an empty result so an outage can never wipe the table.
        gone = airtable.prune(token, base_id, ref, {r["Record Key"] for r in rows}) if rows else 0
        print(f"upserted {n} rows into '{ref}', removed {gone} stale rows")


def _write_reports(records, today, top) -> list:
    REPORTS.mkdir(exist_ok=True)
    airtable_rows = []
    for period in leaders.PERIODS:
        label, start, end = leaders.period_bounds(period, today)
        rows, deals = leaders.rank(records, start, end, top), leaders.mna(records, start, end)
        ipo_deals = leaders.ipos(records, start, end)
        name = f"leaders_{label.replace(' ', '_')}.md"
        (REPORTS / name).write_text(leaders.to_markdown(rows, label, deals, ipo_deals))
        airtable_rows += leaders.to_airtable(rows, label)
        print(f"report: reports/{name} ({len(rows)} funded companies, {len(deals)} M&A, "
              f"{len(ipo_deals)} IPOs)")
    return airtable_rows


def cmd_leaders(args):
    records = _load_snapshot()
    today = parse_date(args.as_of) if args.as_of else date.today()
    if args.date_from:
        label, start, end = leaders.custom_bounds(
            parse_date(args.date_from), parse_date(args.date_to) if args.date_to else today)
    else:
        label, start, end = leaders.period_bounds(args.period, today)
    rows, deals = leaders.rank(records, start, end, args.top), leaders.mna(records, start, end)
    print(leaders.to_markdown(rows, label, deals, leaders.ipos(records, start, end)))


def cmd_inspect(args):
    token = _env("APIFY_TOKEN")
    actors = [s["actor"] for s in json.loads(Path(args.sources).read_text())]
    actors.append(os.environ.get("GOOGLE_SEARCH_ACTOR") or linkedin.GOOGLE_ACTOR)
    for actor in actors:
        try:
            print(apify.describe_actor(token, actor))
        except Exception as exc:  # noqa: BLE001 - report and continue with the next actor
            print(f"=== {actor}\n  could not inspect: {exc}")
        print()


def main(argv=None):
    p = argparse.ArgumentParser(prog="collector", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="create the Airtable base / tables")
    s.add_argument("--name", default="Tracxn Research")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("sync", help="scrape via Apify, update snapshot, reports and Airtable")
    s.add_argument("--since", help="YYYY-MM-DD; default START_DATE or 1 Apr of this FY")
    s.add_argument("--sources", default=str(ROOT / "sources.json"),
                   help="JSON list of Apify actors to pull deals from")
    s.add_argument("--from-file", help="use a saved Apify dataset JSON instead of running actors")
    s.add_argument("--all-stages", action="store_true",
                   help="keep every round (seed, debt...), not just Series A+ and M&A")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--no-airtable", action="store_true")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("leaders", help="print top-funded companies and M&A from the snapshot")
    s.add_argument("--period", choices=leaders.PERIODS, default="last-month")
    s.add_argument("--from", dest="date_from", help="YYYY-MM-DD; custom range instead of --period")
    s.add_argument("--to", dest="date_to", help="YYYY-MM-DD; default today")
    s.add_argument("--as-of", help="YYYY-MM-DD; default today")
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(func=cmd_leaders)

    s = sub.add_parser("inspect", help="print each source actor's input schema and last run log")
    s.add_argument("--sources", default=str(ROOT / "sources.json"))
    s.set_defaults(func=cmd_inspect)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as exc:
        if "api.airtable.com" in str(exc) and "-> 401" in str(exc):
            sys.exit(f"{exc}\n\nAirtable rejected the token. Check the AIRTABLE_PAT secret: "
                     "paste only the token (patXXXX.XXXX...), no quotes or 'Bearer'.")
        if "api.airtable.com" in str(exc) and "-> 403" in str(exc):
            sys.exit(f"{exc}\n\nThe token works but can't access this base. In Airtable's "
                     "token settings add the Tracxn Database base and the scopes "
                     "data.records:read/write and schema.bases:read/write.")
        raise


if __name__ == "__main__":
    main()
