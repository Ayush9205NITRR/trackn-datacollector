"""Turn raw Apify dataset items into FundingRecords.

Items come from several actors (a funding-rounds database, Indian startup news
feeds, Tracxn profiles), each naming fields differently, so every canonical field
is looked up from a list of candidate keys. Add a key to FIELD_ALIASES if an
actor uses a name that is not listed here.
"""

import os
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional

from .schema import FUNDING, IPO, MNA, FundingRecord

FIELD_ALIASES = {
    "company": ["companyName", "company_name", "company", "startupName", "startup",
                "organization", "organizationName", "targetCompany", "target", "name"],
    "domain": ["domain", "website", "companyWebsite", "companyDomain", "url_domain"],
    "tracxn_url": ["canonicalUrl", "tracxnUrl", "tracxn_url", "profileUrl"],
    "source_url": ["sourceUrl", "articleUrl", "link", "sourceLink", "url"],
    "source": ["source", "publisher", "sourceName"],
    "headline": ["headline", "title", "articleTitle"],
    "sector": ["sector", "industry", "category", "vertical", "practiceArea", "feed"],
    "location": ["location", "hq", "headquarters", "city", "hqCity"],
    "country": ["country", "hqCountry", "countryCode"],
    "employee_band": ["employeeCount", "employee_count", "employeeSize",
                      "employees", "employeeRange", "teamSize", "headcount"],
    "stage": ["latestFundingRound", "fundingStage", "roundType", "round_type",
              "fundingRound", "stage", "roundName", "round", "lastRoundType",
              "companyStage"],
    "amount": ["amountUsd", "amountUSD", "amount_usd", "latestFundingAmount",
               "lastFundingAmount", "roundAmount", "fundingAmount", "amount"],
    "round_date": ["latestFundingDate", "lastFundingDate", "roundDate", "fundingDate",
                   "announcedDate", "announcedOn", "announced_date", "dealDate",
                   "filingDate", "publishedAt", "pubDate", "date"],
    "post_date": ["publishedAt", "pubDate", "published", "publishDate", "publishedDate",
                  "articleDate"],
    "investors": ["investors", "latestRoundInvestors", "leadInvestors", "leadInvestor",
                  "lead_investor", "investorNames", "backedBy", "institutionalInvestors"],
    "acquirer": ["acquirer", "acquirerName", "acquiredBy", "buyer"],
    "deal_type": ["dealType", "deal_type", "recordType", "transactionType", "newsCategory",
                  "type", "newsType"],
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
    inr = bool(re.search(r"₹|\brs\.?|\binr\b", text))
    text = re.sub(r"₹|\brs\.?|\binr\b|\$|\busd\b", " ", text)
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    number *= _MULTIPLIERS.get((m.group(2) or "").lower(), 1)
    return number / float(os.environ.get("INR_PER_USD", "88")) if inr else number


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
    try:  # RSS style: "Tue, 29 Sep 2026 06:34:49 +0000"
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
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


_SERIES = re.compile(r"\bseries\s*[a-z]\d*\b|growth|late[-\s]stage|private equity|"
                     r"\bpe\b|mezzanine|secondary|buyout|corporate round", re.I)
_NOT_A_PLUS = re.compile(r"pre[-\s]?series|seed|angel|grant|debt|convertible|"
                         r"crowdfund|\bipo\b", re.I)
_MNA_TYPE = re.compile(r"acqui|merger|\bm\s*&\s*a\b|^\s*ma\s*$|takeover", re.I)
_MNA_HEADLINE = re.compile(r"\b(acquires?|acquired|acquisition|acqui-?hires?|merges?|"
                           r"merger|buys|takes over|takeover)\b", re.I)
_FUNDING_HEADLINE = re.compile(r"\b(raises?|raised|secures?|bags?|gets|funding|"
                               r"investment)\b", re.I)
_ACQUIRES = re.compile(r"^(?P<acq>.+?)\s+(?:acquires|buys|acqui-hires|takes over|to acquire)\s+"
                       r"(?P<target>.+?)(?:\s+(?:for|in|at|to|from|amid)\s|[,:;]|$)", re.I)
_ACQUIRED_BY = re.compile(r"^(?P<target>.+?)\s+(?:is\s+|gets\s+)?acquired by\s+"
                          r"(?P<acq>.+?)(?:\s+(?:for|in|at|amid)\s|[,:;]|$)", re.I)
_RAISES = re.compile(r"^(?P<company>.+?)\s+(?:raises|secures|bags|gets|nets|closes)\s", re.I)
# Needs a size unit, so share prices like "priced at Rs 1,800" aren't read as deal size.
_HEADLINE_MONEY = re.compile(r"(?:\$|₹|\brs\.?|\binr\b|\busd\b)\s?[\d.,]+\s?"
                            r"(?:mn|million|m|bn|billion|b|cr|crore|lakh|k)\b", re.I)
_LED_BY = re.compile(r"\bled by\s+(.+?)(?:\s+(?:and|with|at|in|for|to)\s+(?:others|participation)"
                     r"|[,:;]|$)", re.I)
_DESCRIPTOR = re.compile(r".*\b(?:startup|major|giant|unicorn|firm|company|platform|"
                         r"player|maker|brand|provider|marketplace|operator|parent)\s+", re.I)
# "AceVector’s IPO", "Moneyview IPO", "NSE makes muted debut" -> the company alone
_IPO_TAIL = re.compile(r"(?:['’]s)?\s+(?:ipo\b|makes|files|lists|debuts|opens|gets|to\b|set\b|"
                       r"shares\b|sees|subscribed).*$", re.I)


def _clean_name(text: str) -> str:
    """'quick commerce startup Blinkit' -> 'Blinkit'; strip possessives/punctuation."""
    text = _DESCRIPTOR.sub("", text.strip())
    text = re.sub(r"^(?:[a-z0-9-]+\s+)+(?=[A-Z0-9])", "", text)  # leading lowercase words
    return re.sub(r"['’]s$", "", text).strip(" .,'\"‘’“”")


def is_series_a_plus(stage: str) -> bool:
    stage = stage or ""
    if re.search(r"pre[-\s]?ipo", stage, re.I):
        return True
    return not _NOT_A_PLUS.search(stage) and bool(_SERIES.search(stage))


def qualifies(rec: FundingRecord, min_unlabeled_usd: float) -> bool:
    """M&A, IPOs, Series A-or-later rounds, and unlabeled rounds of at least
    min_unlabeled_usd."""
    if rec.deal_type in (MNA, IPO):
        return True
    if rec.stage:
        return is_series_a_plus(rec.stage)
    return (rec.amount_usd or 0) >= min_unlabeled_usd


# News that mentions money but isn't a startup raising, listing or being acquired.
_NOT_A_DEAL = re.compile(r"rights issue|block deals?|bulk deals?|offloads?|"
                         r"stake sale|sells .*stake|first close|final close|"
                         r"\bfund\s+(?:[ivx]+|\d+)\b|launches .*\bfund\b", re.I)
# Categories that are deals; anything else a source labels (exit, layoffs...) is not.
_DEAL_CATEGORIES = re.compile(r"fund|invest|round|acqui|merger|m\s*&\s*a|ipo|listing", re.I)
_PRE_IPO = re.compile(r"pre[-\s]?ipo", re.I)
_IPO_TYPE = re.compile(r"\bipo\b|listing", re.I)
_IPO_HEADLINE = re.compile(r"\bipo\b|\bdrhp\b|\brhp\b|lists on|listing|market debut|"
                           r"makes .*debut", re.I)


def _deal_type(item: dict, headline: str) -> Optional[str]:
    """FUNDING, MNA, IPO, or None when the item isn't a deal we track."""
    kind = str(_pick(item, "deal_type") or "")
    if _MNA_TYPE.search(kind) or _pick(item, "acquirer"):
        return MNA
    pre_ipo = _PRE_IPO.search(kind) or _PRE_IPO.search(headline or "")
    if not pre_ipo and (_IPO_TYPE.search(kind) or _IPO_TYPE.search(str(_pick(item, "stage") or ""))):
        return IPO
    if kind and not _DEAL_CATEGORIES.search(kind):
        return None
    if headline and not pre_ipo and _IPO_HEADLINE.search(headline):
        return IPO
    if headline and _NOT_A_DEAL.search(headline):
        return None
    if headline and _MNA_HEADLINE.search(headline) and not _FUNDING_HEADLINE.search(headline):
        return MNA
    return FUNDING


def normalize_item(item: dict) -> Optional[FundingRecord]:
    if item.get("status") not in (None, "success"):
        return None
    # Some actors nest the latest round under a key; flatten it in.
    latest = item.get("latestRound") or item.get("lastFundingRound")
    if isinstance(latest, dict):
        item = {**item, **{f"latest{k[0].upper()}{k[1:]}": v for k, v in latest.items()},
                **latest}
    # Structured fields win; the Tracxn summary sentence fills whatever is missing.
    summary = parse_funding_summary(item.get("fundingSummary"))
    item = {**summary, **{k: v for k, v in item.items() if v not in (None, "", [])}}

    headline = str(_pick(item, "headline") or "")
    deal_type = _deal_type(item, headline)
    if deal_type is None:
        return None
    company = _pick(item, "company")
    acquirer = str(_pick(item, "acquirer") or "")
    if deal_type == MNA and headline and not acquirer:
        # The headline says who bought whom; a feed's "company" may be either side.
        m = _ACQUIRES.search(headline) or _ACQUIRED_BY.search(headline)
        if m:
            company = _clean_name(m.group("target")) or company
            acquirer = _clean_name(m.group("acq"))
    if not company and headline and (m := _RAISES.search(headline)):
        company = _clean_name(m.group("company"))
    if deal_type == IPO:  # "AceVector’s IPO", "EverBrands files DRHP..." -> company
        company = _IPO_TAIL.sub("", str(company or headline)).strip()
    if not company:
        return None
    if headline:  # news feeds often keep the descriptor: "Enterprise AI Startup Ema"
        company = _clean_name(str(company)) or company
        # Feeds Title-case names ("Nse"); keep the headline's casing when it's there.
        at = headline.lower().find(str(company).lower())
        if at >= 0:
            company = headline[at:at + len(str(company))]

    amount = _pick(item, "amount")
    if isinstance(amount, (int, float)) and item.get("_amount_multiplier"):
        amount = amount * item["_amount_multiplier"]
    if amount is None and headline and (m := _HEADLINE_MONEY.search(headline)):
        amount = m.group(0)
    investors = _pick(item, "investors")
    if not investors and deal_type == FUNDING and headline and (m := _LED_BY.search(headline)):
        investors = m.group(1)

    url = str(_pick(item, "source_url") or "").strip()
    tracxn_url = str(_pick(item, "tracxn_url") or "").strip()
    if "tracxn.com/d/companies/" in url:
        tracxn_url, url = tracxn_url or url, ""
    if deal_type == FUNDING and not (_pick(item, "stage") or amount or item.get("fundingSummary")):
        return None  # general news (leadership, regulation...), not a deal
    round_date = parse_date(_pick(item, "round_date"))
    # Post date = when the article/announcement was published; for news items
    # without a separate publish field, that is the item's own date.
    post_date = parse_date(_pick(item, "post_date")) or (round_date if headline else None)
    return FundingRecord(
        company=str(company).strip(),
        round_date=round_date,
        post_date=post_date,
        amount_usd=parse_amount(amount),
        stage=str(_pick(item, "stage") or "").strip() if deal_type == FUNDING else "",
        investors=parse_investors(investors),
        employee_band=employee_band(_pick(item, "employee_band")),
        domain=str(_pick(item, "domain") or "").strip(),
        tracxn_url=tracxn_url,
        sector=str(_pick(item, "sector") or "").strip(),
        location=str(_pick(item, "location") or "").strip(),
        total_funding_usd=parse_amount(_pick(item, "total_funding")),
        deal_type=deal_type,
        acquirer=acquirer.strip(),
        country=str(_pick(item, "country") or "").strip(),
        source=str(item.get("_source") or _pick(item, "source") or "").strip(),
        source_url=url,
    )


_COUNTRY_CODES = {"india": "in", "united states": "us", "united kingdom": "gb",
                  "singapore": "sg", "united arab emirates": "ae"}


def _same_country(wanted: str, actual: str) -> bool:
    wanted, actual = wanted.strip().lower(), actual.strip().lower()
    return wanted in actual or _COUNTRY_CODES.get(wanted) == actual


def normalize(items: Iterable[dict], since: Optional[date] = None,
              until: Optional[date] = None, country: str = "",
              min_unlabeled_usd: Optional[float] = None) -> list:
    """Normalize, keep deals in [since, until] that qualify (Series A+ or M&A),
    and merge duplicate reports of the same deal from different sources.

    Pass min_unlabeled_usd=None to keep every deal regardless of stage.
    """
    records = {}
    for item in items:
        rec = normalize_item(item)
        if rec is None or rec.round_date is None:
            continue
        if (since and rec.round_date < since) or (until and rec.round_date > until):
            continue
        if country and rec.country and not _same_country(country, rec.country):
            continue
        if min_unlabeled_usd is not None and not qualifies(rec, min_unlabeled_usd):
            continue
        records[rec.key] = records[rec.key].merge(rec) if rec.key in records else rec
    return list(records.values())
