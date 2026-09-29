"""Canonical record shape for the research table and its Airtable schema."""

import re
from dataclasses import dataclass, field, asdict, fields as dc_fields
from datetime import date
from typing import Optional

FUNDING_TABLE = "Funding Rounds"
LEADERS_TABLE = "Monthly Leaders"

FUNDING = "Funding"
MNA = "M&A"
IPO = "IPO"

_CURRENCY = {"precision": 0, "symbol": "$"}
_ISO_DATE = {"dateFormat": {"name": "iso"}}

# Airtable Meta API field definitions, used by `setup` to create/extend tables.
FUNDING_FIELDS = [
    {"name": "Record Key", "type": "singleLineText"},
    {"name": "Company", "type": "singleLineText"},
    {"name": "Deal Type", "type": "singleLineText"},
    {"name": "Domain", "type": "singleLineText"},
    {"name": "Tracxn URL", "type": "url"},
    {"name": "Sector", "type": "singleLineText"},
    {"name": "Location", "type": "singleLineText"},
    {"name": "Country", "type": "singleLineText"},
    {"name": "Employee Size Band", "type": "singleLineText"},
    {"name": "Funding Stage", "type": "singleLineText"},
    {"name": "Last Funding Amount (USD)", "type": "currency", "options": _CURRENCY},
    {"name": "Last Funding Date", "type": "date", "options": _ISO_DATE},
    {"name": "Post Date", "type": "date", "options": _ISO_DATE},
    {"name": "Backed By", "type": "multilineText"},
    {"name": "Acquirer", "type": "singleLineText"},
    {"name": "Total Funding (USD)", "type": "currency", "options": _CURRENCY},
    {"name": "Month", "type": "singleLineText"},
    {"name": "Quarter", "type": "singleLineText"},
    {"name": "Source", "type": "singleLineText"},
    {"name": "Source URL", "type": "url"},
    {"name": "Headline", "type": "multilineText"},
    {"name": "Last Synced", "type": "date", "options": _ISO_DATE},
]

LEADERS_FIELDS = [
    {"name": "Record Key", "type": "singleLineText"},
    {"name": "Period", "type": "singleLineText"},
    {"name": "Rank", "type": "number", "options": {"precision": 0}},
    {"name": "Company", "type": "singleLineText"},
    {"name": "Employee Size Band", "type": "singleLineText"},
    {"name": "Funding Stage", "type": "singleLineText"},
    {"name": "Last Funding Amount (USD)", "type": "currency", "options": _CURRENCY},
    {"name": "Raised In Period (USD)", "type": "currency", "options": _CURRENCY},
    {"name": "Last Funding Date", "type": "date", "options": _ISO_DATE},
    {"name": "Post Date", "type": "date", "options": _ISO_DATE},
    {"name": "Backed By", "type": "multilineText"},
    {"name": "Tracxn URL", "type": "url"},
]

_LEGAL_SUFFIX = re.compile(
    r"\b(private|pvt|limited|ltd|llp|inc|technologies|technology)\b\.?", re.I)


def company_ident(name: str) -> str:
    """Normalized company name so 'Acme Technologies Pvt. Ltd.' == 'Acme'."""
    text = _LEGAL_SUFFIX.sub(" ", name.lower())
    return re.sub(r"[^a-z0-9]+", "", text) or re.sub(r"[^a-z0-9]+", "", name.lower())


def fiscal_quarter(d: date) -> str:
    """Indian fiscal quarter: FY27 Q1 = Apr–Jun 2026, Q4 = Jan–Mar 2027."""
    fy = d.year + 1 if d.month >= 4 else d.year
    q = (d.month - 4) % 12 // 3 + 1
    return f"FY{fy % 100:02d} Q{q}"


@dataclass
class FundingRecord:
    company: str
    round_date: Optional[date]
    amount_usd: Optional[float]
    stage: str = ""
    investors: list = field(default_factory=list)
    employee_band: str = ""
    domain: str = ""
    tracxn_url: str = ""
    sector: str = ""
    location: str = ""
    total_funding_usd: Optional[float] = None
    deal_type: str = FUNDING
    acquirer: str = ""
    country: str = ""
    source: str = ""
    source_url: str = ""
    post_date: Optional[date] = None  # when the news article / announcement was published
    headline: str = ""

    @property
    def ident(self) -> str:
        return company_ident(self.company)

    @property
    def key(self) -> str:
        """Same deal reported by different sources → same key (month granularity,
        since news and databases often disagree on the exact day)."""
        what = self.stage.strip().lower() if self.deal_type == FUNDING else self.deal_type.lower()
        return f"{self.ident}|{what}|{self.month or 'unknown'}"

    @property
    def month(self) -> str:
        return self.round_date.strftime("%Y-%m") if self.round_date else ""

    @property
    def quarter(self) -> str:
        return fiscal_quarter(self.round_date) if self.round_date else ""

    def merge(self, other: "FundingRecord") -> "FundingRecord":
        """Combine two reports of the same deal: keep our values, fill gaps from other."""
        values = {}
        for f in dc_fields(self):
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            if f.name == "investors":
                values[f.name] = mine + [i for i in theirs if i not in mine]
            elif f.name == "source":
                parts = [p for p in (mine, theirs) if p]
                values[f.name] = ", ".join(dict.fromkeys(", ".join(parts).split(", ")))
            elif f.name == "post_date":  # first time the deal was reported
                values[f.name] = min((d for d in (mine, theirs) if d), default=None)
            else:
                values[f.name] = mine if mine not in (None, "") else theirs
        return FundingRecord(**values)

    def to_airtable(self, synced: date) -> dict:
        fields = {
            "Record Key": self.key,
            "Company": self.company,
            "Deal Type": self.deal_type,
            "Domain": self.domain,
            "Tracxn URL": self.tracxn_url or None,
            "Sector": self.sector,
            "Location": self.location,
            "Country": self.country,
            "Employee Size Band": self.employee_band,
            "Funding Stage": self.stage,
            "Last Funding Amount (USD)": self.amount_usd,
            "Last Funding Date": self.round_date.isoformat() if self.round_date else None,
            "Post Date": self.post_date.isoformat() if self.post_date else None,
            "Backed By": ", ".join(self.investors),
            "Acquirer": self.acquirer,
            "Total Funding (USD)": self.total_funding_usd,
            "Month": self.month,
            "Quarter": self.quarter,
            "Source": self.source,
            "Source URL": self.source_url or None,
            "Headline": self.headline,
            "Last Synced": synced.isoformat(),
        }
        return {k: v for k, v in fields.items() if v not in (None, "")}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["round_date"] = self.round_date.isoformat() if self.round_date else None
        d["post_date"] = self.post_date.isoformat() if self.post_date else None
        return d
