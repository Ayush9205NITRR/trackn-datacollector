"""Run the automation-lab Tracxn scraper on Apify and fetch its dataset items."""

import json
import time
import urllib.parse

from .http import request_json, request_text

API = "https://api.apify.com/v2"
DEFAULT_ACTOR = "automation-lab/tracxn-company-intelligence-scraper"


def load_urls(path: str) -> list:
    """Read Tracxn company-profile URLs, one per line; '#' starts a comment."""
    urls = []
    with open(path) as f:
        for line in f:
            url = line.split("#", 1)[0].strip()
            if not url:
                continue
            url = url.split("?", 1)[0].rstrip("/")
            if "tracxn.com/d/companies/" not in url:
                raise ValueError(f"not a Tracxn company-profile URL: {url}")
            if url not in urls:
                urls.append(url)
    return urls


def build_input(urls: list) -> dict:
    return {"startUrls": [{"url": u} for u in urls]}


def run_actor(token: str, actor_id: str, actor_input: dict,
              poll_seconds: int = 15, max_wait_seconds: int = 3600) -> list:
    """Start the actor, wait for it to finish, and return all dataset items."""
    actor = urllib.parse.quote(actor_id.replace("/", "~"), safe="~")
    run = request_json("POST", f"{API}/acts/{actor}/runs", token, actor_input)["data"]
    run_id = run["id"]
    waited = 0
    while run["status"] in ("READY", "RUNNING"):
        if waited >= max_wait_seconds:
            raise TimeoutError(f"Apify run {run_id} still {run['status']} after {waited}s")
        time.sleep(poll_seconds)
        waited += poll_seconds
        run = request_json("GET", f"{API}/actor-runs/{run_id}", token)["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id} ended with status {run['status']}")
    return fetch_dataset(token, run["defaultDatasetId"])


def describe_actor(token: str, actor_id: str, log_lines: int = 25) -> str:
    """Input schema (field names, types, allowed values) and the tail of our last run's
    log, to line sources.json up with what the actor really accepts."""
    actor = urllib.parse.quote(actor_id.replace("/", "~"), safe="~")
    out = [f"=== {actor_id}"]
    info = request_json("GET", f"{API}/acts/{actor}", token)["data"]
    build_id = (info.get("taggedBuilds") or {}).get("latest", {}).get("buildId")
    schema = {}
    if build_id:
        build = request_json("GET", f"{API}/actor-builds/{build_id}", token)["data"]
        raw = build.get("inputSchema") or (build.get("actorDefinition") or {}).get("input")
        schema = json.loads(raw) if isinstance(raw, str) else (raw or {})
    required = set(schema.get("required", []))
    for name, prop in (schema.get("properties") or {}).items():
        details = [prop.get("type", "?")]
        for key in ("enum", "default", "prefill", "minimum", "maximum"):
            if key in prop:
                details.append(f"{key}={json.dumps(prop[key])[:200]}")
        flag = " (required)" if name in required else ""
        out.append(f"  {name}{flag}: {', '.join(details)} — {prop.get('title', '')}")
        if prop.get("description"):
            out.append(f"      {prop['description'][:200]}")
    runs = request_json("GET", f"{API}/acts/{actor}/runs?desc=1&limit=1", token)["data"]["items"]
    if runs:
        run = runs[0]
        out.append(f"  last run {run['id']}: {run['status']}, started {run.get('startedAt')}")
        log = request_text(f"{API}/logs/{run['id']}", token).splitlines()
        out += [f"    | {line}" for line in log[-log_lines:]]
    return "\n".join(out)


def fetch_dataset(token: str, dataset_id: str, page_size: int = 1000) -> list:
    items, offset = [], 0
    while True:
        page = request_json(
            "GET",
            f"{API}/datasets/{dataset_id}/items?clean=true&format=json"
            f"&offset={offset}&limit={page_size}",
            token,
        )
        items.extend(page)
        if len(page) < page_size:
            return items
        offset += page_size
