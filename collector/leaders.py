"""Rank companies by how much they raised in a period, and list M&A deals in it.

Periods use the Indian fiscal year: FY27 = 1 Apr 2026 – 31 Mar 2027,
FY27 Q1 = Apr–Jun 2026, Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar.
"""

from datetime import date, timedelta

from .schema import FUNDING, IPO, MNA, fiscal_quarter

PERIODS = ("last-month", "last-quarter", "fy-to-date")


def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def period_bounds(period: str, today: date) -> tuple:
    """Return (label, start, end) inclusive."""
    first_this_month = today.replace(day=1)
    if period == "last-month":
        end = first_this_month - timedelta(days=1)
        start = end.replace(day=1)
        return start.strftime("%Y-%m"), start, end
    if period == "last-quarter":
        q_start_month = 3 * ((today.month - 1) // 3) + 1
        end = today.replace(month=q_start_month, day=1) - timedelta(days=1)
        start = end.replace(month=3 * ((end.month - 1) // 3) + 1, day=1)
        return fiscal_quarter(start), start, end
    if period == "fy-to-date":
        start = fy_start(today)
        return f"FY{(start.year + 1) % 100:02d} YTD", start, today
    raise ValueError(f"unknown period {period!r}; use one of {', '.join(PERIODS)}")


def custom_bounds(start: date, end: date) -> tuple:
    q1, q2 = fiscal_quarter(start), fiscal_quarter(end)
    label = q1 if q1 == q2 else f"{q1}–{q2.split()[-1]}" if q1[:4] == q2[:4] else f"{q1}–{q2}"
    return label, start, end


def _in_period(rec, start, end) -> bool:
    return rec.round_date is not None and start <= rec.round_date <= end


def rank(records: list, start: date, end: date, top: int = 25) -> list:
    """Group funding rounds in [start, end] by company, rank by total raised.

    Each result carries the company's latest round in the period (stage, amount,
    investors) plus the period total, which is what "best quarter" is ranked on.
    """
    by_company = {}
    for rec in records:
        if rec.deal_type != FUNDING or not _in_period(rec, start, end):
            continue
        entry = by_company.setdefault(rec.ident, {"raised": 0.0, "latest": rec, "investors": [],
                                                  "employee_band": ""})
        entry["raised"] += rec.amount_usd or 0.0
        if rec.round_date >= entry["latest"].round_date:
            entry["latest"] = rec
        entry["employee_band"] = entry["employee_band"] or rec.employee_band
        for inv in rec.investors:
            if inv not in entry["investors"]:
                entry["investors"].append(inv)

    ordered = sorted(by_company.values(), key=lambda e: e["raised"], reverse=True)
    rows = []
    for i, e in enumerate(ordered[:top], start=1):
        latest = e["latest"]
        rows.append({
            "rank": i,
            "company": latest.company,
            "employee_band": latest.employee_band or e["employee_band"],
            "stage": latest.stage,
            "last_amount_usd": latest.amount_usd,
            "raised_in_period_usd": e["raised"],
            "backed_by": e["investors"],
            "tracxn_url": latest.tracxn_url,
            "round_date": latest.round_date,
            "post_date": latest.post_date,
        })
    return rows


def deals_of(records: list, start: date, end: date, deal_type: str) -> list:
    deals = [r for r in records if r.deal_type == deal_type and _in_period(r, start, end)]
    return sorted(deals, key=lambda r: r.round_date, reverse=True)


def mna(records: list, start: date, end: date) -> list:
    return deals_of(records, start, end, MNA)


def ipos(records: list, start: date, end: date) -> list:
    return deals_of(records, start, end, IPO)


def to_airtable(rows: list, label: str) -> list:
    out = []
    for r in rows:
        fields = {
            "Record Key": f"{label}|{r['rank']}",
            "Period": label,
            "Rank": r["rank"],
            "Company": r["company"],
            "Employee Size Band": r["employee_band"],
            "Funding Stage": r["stage"],
            "Last Funding Amount (USD)": r["last_amount_usd"],
            "Raised In Period (USD)": r["raised_in_period_usd"],
            "Last Funding Date": _iso(r.get("round_date")),
            "Post Date": _iso(r.get("post_date")),
            "Backed By": ", ".join(r["backed_by"]),
            "Tracxn URL": r["tracxn_url"] or None,
        }
        out.append({k: v for k, v in fields.items() if v not in (None, "")})
    return out


def _money(v) -> str:
    if not v:
        return "undisclosed"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"${v / div:.1f}{suffix}"
    return f"${v:,.0f}"


def _iso(d):
    return d.isoformat() if d else None


def _link(d) -> str:
    return f"[{d.source or 'link'}]({d.source_url})" if d.source_url else (d.source or "—")


def to_markdown(rows: list, label: str, deals: list = (), ipo_deals: list = ()) -> str:
    lines = [f"## Top funded companies — {label}", "",
             "| # | Company | Employee band | Stage | Last round | Raised in period | Backed by "
             "| Round date | Post date |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['rank']} | {r['company']} | {r['employee_band'] or '—'} | "
            f"{r['stage'] or '—'} | {_money(r['last_amount_usd'])} | "
            f"{_money(r['raised_in_period_usd'])} | {', '.join(r['backed_by']) or '—'} | "
            f"{_iso(r.get('round_date')) or '—'} | {_iso(r.get('post_date')) or '—'} |")
    if deals:
        lines += ["", f"## M&A — {label}", "",
                  "| Date | Company | Acquirer | Amount | Employee band | Post date | Source |",
                  "|---|---|---|---|---|---|---|"]
        for d in deals:
            lines.append(
                f"| {d.round_date.isoformat()} | {d.company} | {d.acquirer or '—'} | "
                f"{_money(d.amount_usd)} | {d.employee_band or '—'} | "
                f"{_iso(d.post_date) or '—'} | {_link(d)} |")
    if ipo_deals:
        lines += ["", f"## IPOs — {label}", "",
                  "| Date | Company | Issue size | Post date | Source |",
                  "|---|---|---|---|---|"]
        for d in ipo_deals:
            lines.append(
                f"| {d.round_date.isoformat()} | {d.company} | {_money(d.amount_usd)} | "
                f"{_iso(d.post_date) or '—'} | {_link(d)} |")
    return "\n".join(lines) + "\n"
