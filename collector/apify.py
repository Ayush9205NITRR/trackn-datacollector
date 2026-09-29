"""Run a Tracxn scraper actor on Apify and fetch its dataset items."""

import json
import time
import urllib.parse

from .http import request_json

API = "https://api.apify.com/v2"


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


def load_input(path: str, since: str, until: str) -> dict:
    """Read the actor input template and fill in {since}/{until} placeholders."""
    with open(path) as f:
        text = f.read()
    return json.loads(text.replace("{since}", since).replace("{until}", until))
