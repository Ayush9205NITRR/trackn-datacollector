"""Canonical record shape for the research table and its Airtable schema."""

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

FUNDING_TABLE = "Funding Rounds"
LEADERS_TABLE = "Monthly Leaders"

# Airtable Meta API field definitions, used by `setup` to create the base/tables.
FUNDING_FIELDS = [
    {"name": "Record Key", "type": "singleLineText"},
    {"name": "Company", "type": "singleLineText"},
    {"name": "Domain", "type": "singleLineText"},
    {"name": "Tracxn URL", "type": "url"},
    {"name": "Sector", "type": "singleLineText"},
    {"name": "Location", "type": "singleLineText"},
    {"name": "Employee Size Band", "type": "singleLineText"},
    {"name": "Funding Stage", "type": "singleLineText"},
    {"name": "Last Funding Amount (USD)", "type": "currency",
     "options": {"precision": 0, "symbol": "$"}},
    {"name": "Last Funding Date", "type": "date",
     "options": {"dateFormat": {"name": "iso"}}},
    {"name": "Backed By", "type": "multilineText"},
    {"name": "Total Funding (USD)", "type": "currency",
     "options": {"precision": 0, "symbol": "$"}},
    {"name": "Month", "type": "singleLineText"},
    {"name": "Quarter", "type": "singleLineText"},
    {"name": "Last Synced", "type": "date",
     "options": {"dateFormat": {"name": "iso"}}},
]

LEADERS_FIELDS = [
    {"name": "Record Key", "type": "singleLineText"},
    {"name": "Period", "type": "singleLineText"},
    {"name": "Rank", "type": "number", "options": {"precision": 0}},
    {"name": "Company", "type": "singleLineText"},
    {"name": "Employee Size Band", "type": "singleLineText"},
    {"name": "Funding Stage", "type": "singleLineText"},
    {"name": "Last Funding Amount (USD)", "type": "currency",
     "options": {"precision": 0, "symbol": "$"}},
    {"name": "Raised In Period (USD)", "type": "currency",
     "options": {"precision": 0, "symbol": "$"}},
    {"name": "Backed By", "type": "multilineText"},
    {"name": "Tracxn URL", "type": "url"},
]


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

    @property
    def ident(self) -> str:
        """Stable company identity: Tracxn profile URL, else domain, else name."""
        return (self.tracxn_url or self.domain or self.company).strip().lower()

    @property
    def key(self) -> str:
        ident = self.ident
        day = self.round_date.isoformat() if self.round_date else "unknown"
        return f"{ident}|{day}|{self.stage.strip().lower()}"

    @property
    def month(self) -> str:
        return self.round_date.strftime("%Y-%m") if self.round_date else ""

    @property
    def quarter(self) -> str:
        if not self.round_date:
            return ""
        return f"{self.round_date.year}-Q{(self.round_date.month - 1) // 3 + 1}"

    def to_airtable(self, synced: date) -> dict:
        fields = {
            "Record Key": self.key,
            "Company": self.company,
            "Domain": self.domain,
            "Tracxn URL": self.tracxn_url or None,
            "Sector": self.sector,
            "Location": self.location,
            "Employee Size Band": self.employee_band,
            "Funding Stage": self.stage,
            "Last Funding Amount (USD)": self.amount_usd,
            "Last Funding Date": self.round_date.isoformat() if self.round_date else None,
            "Backed By": ", ".join(self.investors),
            "Total Funding (USD)": self.total_funding_usd,
            "Month": self.month,
            "Quarter": self.quarter,
            "Last Synced": synced.isoformat(),
        }
        return {k: v for k, v in fields.items() if v not in (None, "")}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["round_date"] = self.round_date.isoformat() if self.round_date else None
        return d
