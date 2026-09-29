import json
import time
import urllib.error
import urllib.request


def request_json(method: str, url: str, token: str, body=None, timeout=300, retries=4):
    """JSON request with bearer auth; retries on 429/5xx with exponential backoff."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            detail = err.read().decode(errors="replace")
            raise RuntimeError(f"{method} {url} -> {err.code}: {detail}") from err
