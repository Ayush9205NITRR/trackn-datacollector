"""Find each company's LinkedIn page without scraping LinkedIn itself.

1. Company website: if a source gave the domain, read the homepage and take the
   linkedin.com/company/... link (usually in the footer).
2. Google search (Apify's google-search-scraper): '"Name" site:linkedin.com/company',
   keep the first result whose title or URL matches the company name. The result
   snippet often states the company size, which fills a missing employee band.

Lookups are cached in data/linkedin.json (keyed by normalized company name), so
each company is searched once; misses are retried after RETRY_DAYS.
"""

import json
import re
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from . import apify
from .normalize import employee_band
from .schema import company_ident

GOOGLE_ACTOR = "apify/google-search-scraper"
RETRY_DAYS = 30

_LINKEDIN_URL = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([A-Za-z0-9\-_%.]+)", re.I)
_SIZE = re.compile(r"(?:company size|employees)\s*[:\-–]?\s*(\d[\d,]*\s*[-–]\s*\d[\d,]*|\d[\d,]*\+)"
                   r"|(\d[\d,]*\s*[-–]\s*\d[\d,]*|\d[\d,]*\+)\s+employees", re.I)


def canonical(url: str) -> str:
    m = _LINKEDIN_URL.search(url or "")
    return f"https://www.linkedin.com/company/{m.group(1).rstrip('/.').lower()}" if m else ""


def size_from_snippet(text: str) -> str:
    m = _SIZE.search(text or "")
    return employee_band((m.group(1) or m.group(2)).replace("–", "-")) if m else ""


_INDIA = re.compile(r"\bindia|bengaluru|bangalore|mumbai|delhi|gurugram|gurgaon|noida|"
                    r"hyderabad|chennai|pune|kolkata|ahmedabad|jaipur|kochi|chandigarh|"
                    r"indore|\bpvt\b|private limited", re.I)


def _matches(company: str, url: str, title: str) -> bool:
    """Does this LinkedIn result belong to `company`? Compare normalized names with
    the URL slug ('sima-ai') and the page title ('SiMa.ai | LinkedIn'). Short names
    ('Ema') must match the slug: a title match alone is too loose for them."""
    want = company_ident(company)
    if len(want) < 2:
        return False
    raw_slug = _LINKEDIN_URL.search(url).group(1)
    slug = company_ident(raw_slug.replace("-", " "))
    page = company_ident(re.split(r"\s[|\-–:]\s", title or "")[0])
    first_word = company_ident(re.split(r"[-_]", raw_slug)[0])
    if slug == want or first_word == want or (len(want) >= 4 and slug.startswith(want)):
        return True
    return len(want) >= 5 and (page == want or page.startswith(want))


def pick(company: str, results: list) -> dict:
    """From Google organic results, the matching LinkedIn company page, if any.
    Among matches, one that mentions India (or an Indian city) wins."""
    matches = []
    for r in results:
        url = r.get("url") or r.get("link") or ""
        if _LINKEDIN_URL.search(url) and _matches(company, url, r.get("title", "")):
            matches.append(r)
    if not matches:
        return {}
    text = lambda r: f"{r.get('title', '')} {r.get('description') or r.get('snippet') or ''}"
    best = next((r for r in matches if _INDIA.search(text(r))), matches[0])
    return {"url": canonical(best.get("url") or best.get("link")),
            "employee_band": size_from_snippet(best.get("description") or best.get("snippet"))}


def from_website(domain: str, timeout: int = 10) -> str:
    domain = re.sub(r"^https?://", "", domain.strip()).split("/")[0]
    if not domain:
        return ""
    req = urllib.request.Request(f"https://{domain}", headers={
        "User-Agent": "Mozilla/5.0 (compatible; trackn-datacollector)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(2_000_000).decode(errors="replace")
    except Exception:  # noqa: BLE001 - unreachable sites are just a miss
        return ""
    return canonical(html)


def google_search(token: str, names: list, actor: str = GOOGLE_ACTOR) -> dict:
    """One actor run for all names. Returns {name: organic results}."""
    queries = {f'"{n}" India site:linkedin.com/company': n for n in names}
    # Default google.com: country domains (google.co.in) return goto-redirect links
    # the actor often fails to resolve, which hides the real LinkedIn URLs.
    pages = apify.run_actor(token, actor, {
        "queries": "\n".join(queries), "maxPagesPerQuery": 1,
        "saveHtml": False, "saveHtmlToKeyValueStore": False,
        "includeUnfilteredResults": False})
    out = {}
    for page in pages:
        term = (page.get("searchQuery") or {}).get("term", "")
        if term in queries:
            out.setdefault(queries[term], []).extend(page.get("organicResults") or [])
    return out


def load_cache(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(cache.items())), indent=1) + "\n")


def _due(entry: dict, today: date) -> bool:
    if not entry:
        return True
    if entry.get("url"):
        return False
    checked = date.fromisoformat(entry.get("checked", "2000-01-01"))
    return today - checked >= timedelta(days=RETRY_DAYS)


def enrich(records: list, cache: dict, token: str = "", today: date = None,
           search=google_search, website=from_website) -> dict:
    """Fill linkedin_url (and a missing employee_band) on records in place.
    Returns counts for the log."""
    today = today or date.today()
    todo = {}
    for rec in records:
        if _due(cache.get(rec.ident), today):
            todo.setdefault(rec.ident, rec)
    stats = {"website": 0, "google": 0, "missed": 0}

    for ident, rec in list(todo.items()):
        if rec.domain and (url := website(rec.domain)):
            cache[ident] = {"company": rec.company, "url": url, "via": "website",
                            "checked": today.isoformat()}
            stats["website"] += 1
            del todo[ident]

    if todo and token:
        try:
            found = search(token, [r.company for r in todo.values()])
        except Exception as exc:  # noqa: BLE001 - enrichment must not fail the sync
            print(f"[linkedin] Google search failed: {exc}")
            found = None
        if found is not None:
            for ident, rec in todo.items():
                hit = pick(rec.company, found.get(rec.company, []))
                cache[ident] = {"company": rec.company, "url": hit.get("url", ""),
                                "employee_band": hit.get("employee_band", ""),
                                "via": "google", "checked": today.isoformat()}
                stats["google" if hit else "missed"] += 1

    # The cache is the source of truth for looked-up companies, so a corrected
    # entry in data/linkedin.json replaces whatever an earlier run saved.
    for rec in records:
        entry = cache.get(rec.ident)
        if entry is not None:
            rec.linkedin_url = entry.get("url", "")
        rec.employee_band = rec.employee_band or (entry or {}).get("employee_band", "")
    return stats
