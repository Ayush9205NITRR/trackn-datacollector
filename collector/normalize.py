"""Turn raw Apify dataset items (Tracxn scrapes) into FundingRecords.

Different Apify actors name their output fields differently, so each canonical
field is looked up from a list of candidate keys. Add a key to FIELD_ALIASES if
your actor uses a name that is not listed here.
"""

import re
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from .schema import FundingRecord

FIELD_ALIASES = {
    "company": ["companyName", "company_name", "company", "name", "title"],
    "domain": ["domain", "website", "companyWebsite", "url_domain"],
    "tracxn_url": ["canonicalUrl", "tracxnUrl", "tracxn_url", "profileUrl",
                   "sourceUrl", "url"],
    "sector": ["sector", "industry", "practiceArea", "feed"],
    "location": ["location", "hq", "headquarters", "city", "country"],
    "employee_band": ["employeeCount", "employee_count", "employeeSize",
                      "employees", "employeeRange", "teamSize"],
    "stage": ["latestFundingRound", "fundingStage", "stage", "roundName",
              "round", "lastRoundType", "companyStage"],
    "amount": ["latestFundingAmount", "lastFundingAmount", "roundAmount",
               "amount", "fundingAmount", "amountUsd"],
    "round_date": ["latestFundingDate", "lastFundingDate", "roundDate",
                   "fundingDate", "announcedDate", "date"],
    "investors": ["investors", "latestRoundInvestors", "leadInvestors",
                  "backedBy", "institutionalInvestors"],
    "total_funding": ["totalFunding", "totalFundingAmount", "total_funding",
                      "fundingTotal"],
}

_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
                "b": 1e9, "bn": 1e9, "billion": 1e9, "cr": 1e7, "crore": 1e7,
                "l": 1e5, "lakh": 1e5}
_AMOUNT_RE = re.compile(r"([\d,]*\.?\d+)\s*([a-z]+)?", re.I)

# Standard bands so records from different sources group together.
EMPLOYEE_BANDS = [(10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"),
                  (1000, "501-1000"), (5000, "1001-5000"), (10000, "5001-10000")]


def _pick(item: dict, name: str):
    for key in FIELD_ALIASES[name]:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return None


def parse_amount(value) -> Optional[float]:
    """Parse '$12.5M', 'USD 1.2 Bn', '4,000,000' or 4e6 into a float of USD."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return parse_amount(value.get("usd") or value.get("amount") or value.get("value"))
    text = str(value).strip().lower()
    if not text or text in {"undisclosed", "n/a", "-", "na"}:
        return None
    m = _AMOUNT_RE.search(text.replace("$", " ").replace("usd", " "))
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    return number * _MULTIPLIERS.get((m.group(2) or "").lower(), 1)


def parse_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):  # epoch seconds or millis
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts, timezone.utc).date()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
                "%d-%m-%Y", "%d/%m/%Y", "%b %Y", "%B %Y", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def employee_band(value) -> str:
    """Map a headcount number or range string to a standard band."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        count = int(value)
    else:
        text = str(value)
        numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]
        if not numbers:
            return text.strip()
        count = max(numbers) if "-" in text or "to" in text else numbers[0]
    for upper, band in EMPLOYEE_BANDS:
        if count <= upper:
            return band
    return "10000+"


def parse_investors(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[,;|\n]", value) if p.strip()]
    names = []
    for inv in value:
        name = inv.get("name") if isinstance(inv, dict) else inv
        if name and str(name).strip() not in names:
            names.append(str(name).strip())
    return names


_MONEY = r"\$?\s?([\d.,]+\s?(?:[KMB]n?|Mn|Bn|Cr)?)"
_TOTAL_RE = re.compile(r"total funding of\s+" + _MONEY, re.I)
_LATEST_RE = re.compile(
    r"latest funding round was (?:an? )?(?P<stage>[^.]+?) round"
    r"(?: on (?P<date>[A-Z][a-z]{2,8} \d{1,2}, \d{4}))?"
    r"(?: for " + _MONEY.replace("(", "(?P<amount>", 1) + r")?", re.I)
_INVESTORS_RE = re.compile(
    r"(?:top|major|key|lead|institutional) investors? (?:are|is|include|includes)\s+(.+?)(?:\.\s|\.$|$)",
    re.I)


def parse_funding_summary(text) -> dict:
    """Pull total/latest round details out of Tracxn's public funding sentence, e.g.
    "Acme has raised a total funding of $62.5M over 3 rounds. Its latest funding
    round was a Series B round on Aug 12, 2026 for $40M. ... Its top investors
    are Sequoia, Accel and Blume Ventures."
    """
    out = {}
    if not text:
        return out
    text = str(text)
    if m := _TOTAL_RE.search(text):
        out["totalFunding"] = m.group(1)
    if m := _LATEST_RE.search(text):
        out["latestFundingRound"] = m.group("stage").strip()
        if m.group("date"):
            out["latestFundingDate"] = m.group("date")
        if m.group("amount"):
            out["latestFundingAmount"] = m.group("amount")
    if m := _INVESTORS_RE.search(text):
        names = re.split(r",\s*|\s+and\s+", m.group(1))
        out["investors"] = [n.strip() for n in names if n.strip() and "other" not in n.lower()]
    return out


def normalize_item(item: dict) -> Optional[FundingRecord]:
    if item.get("status") not in (None, "success"):
        return None
    company = _pick(item, "company")
    if not company:
        return None
    # Some actors nest the latest round under a key; flatten it in.
    latest = item.get("latestRound") or item.get("lastFundingRound")
    if isinstance(latest, dict):
        item = {**item, **{f"latest{k[0].upper()}{k[1:]}": v for k, v in latest.items()},
                **latest}
    # Structured fields win; the summary sentence fills whatever is missing.
    summary = parse_funding_summary(item.get("fundingSummary"))
    item = {**summary, **{k: v for k, v in item.items() if v not in (None, "", [])}}
    return FundingRecord(
        company=str(company).strip(),
        round_date=parse_date(_pick(item, "round_date")),
        amount_usd=parse_amount(_pick(item, "amount")),
        stage=str(_pick(item, "stage") or "").strip(),
        investors=parse_investors(_pick(item, "investors")),
        employee_band=employee_band(_pick(item, "employee_band")),
        domain=str(_pick(item, "domain") or "").strip(),
        tracxn_url=str(_pick(item, "tracxn_url") or "").strip(),
        sector=str(_pick(item, "sector") or "").strip(),
        location=str(_pick(item, "location") or "").strip(),
        total_funding_usd=parse_amount(_pick(item, "total_funding")),
    )


def normalize(items: Iterable[dict], since: Optional[date] = None) -> list:
    """Normalize, drop rounds before `since`, and de-duplicate by record key."""
    records = {}
    for item in items:
        rec = normalize_item(item)
        if rec is None:
            continue
        if since and (rec.round_date is None or rec.round_date < since):
            continue
        records[rec.key] = rec
    return list(records.values())
