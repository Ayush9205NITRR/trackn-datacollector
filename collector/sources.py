"""Run every enabled deal source (Apify actor) listed in sources.json.

Each source is {"name", "actor", "enabled", "input"}. Strings in "input" may use
{since}, {until} (YYYY-MM-DD) and {days_back}; a value that is exactly
"{days_back}" becomes a number. A source with "urls_file" instead sends the
Tracxn company URLs in that file as startUrls (automation-lab profile scraper).
"amount_multiplier" scales numeric amounts from a source that reports them in
millions. Raw items are saved per source under raw_dir for debugging field names.
"""

import json
from datetime import date
from pathlib import Path

from . import apify


def load(path: Path) -> list:
    return [s for s in json.loads(path.read_text()) if s.get("enabled", True)]


def fill(value, since: date, until: date):
    days_back = (date.today() - since).days + 1
    if isinstance(value, dict):
        return {k: fill(v, since, until) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, since, until) for v in value]
    if value == "{days_back}":
        return days_back
    if isinstance(value, str):
        return (value.replace("{since}", since.isoformat())
                .replace("{until}", until.isoformat())
                .replace("{days_back}", str(days_back)))
    return value


def build_input(source: dict, root: Path, since: date, until: date):
    """Actor input for a source, or None if it has nothing to do."""
    if "urls_file" in source:
        urls = apify.load_urls(str(root / source["urls_file"]))
        return apify.build_input(urls) if urls else None
    return fill(source.get("input", {}), since, until)


def run_all(token: str, sources: list, root: Path, since: date, until: date,
            raw_dir: Path = None) -> list:
    """Run each source; tag items with _source. A failing source is reported and
    skipped so one broken actor doesn't block the rest."""
    items, failures = [], []
    for source in sources:
        actor_input = build_input(source, root, since, until)
        if actor_input is None:
            print(f"[{source['name']}] nothing to do, skipped")
            continue
        try:
            got = apify.run_actor(token, source["actor"], actor_input)
        except Exception as exc:  # noqa: BLE001 - keep other sources running
            print(f"[{source['name']}] FAILED: {exc}")
            failures.append(source["name"])
            continue
        print(f"[{source['name']}] {len(got)} items")
        if raw_dir:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{source['name']}.json").write_text(json.dumps(got, indent=1) + "\n")
        tags = {"_source": source["name"]}
        if "amount_multiplier" in source:
            tags["_amount_multiplier"] = source["amount_multiplier"]
        items += [{**i, **tags} for i in got]
    if failures and len(failures) == len(sources):
        raise RuntimeError(f"every source failed: {', '.join(failures)}")
    return items
