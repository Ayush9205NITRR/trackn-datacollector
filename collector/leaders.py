"""Rank companies by how much they raised in a period (last month / last quarter)."""

from datetime import date, timedelta


def period_bounds(period: str, today: date) -> tuple:
    """Return (label, start, end) inclusive for 'last-month' or 'last-quarter'."""
    first_this_month = today.replace(day=1)
    if period == "last-month":
        end = first_this_month - timedelta(days=1)
        start = end.replace(day=1)
        return start.strftime("%Y-%m"), start, end
    if period == "last-quarter":
        q_start_month = 3 * ((today.month - 1) // 3) + 1
        end = today.replace(month=q_start_month, day=1) - timedelta(days=1)
        start = end.replace(month=3 * ((end.month - 1) // 3) + 1, day=1)
        return f"{end.year}-Q{(end.month - 1) // 3 + 1}", start, end
    raise ValueError(f"unknown period {period!r}; use last-month or last-quarter")


def rank(records: list, start: date, end: date, top: int = 25) -> list:
    """Group rounds in [start, end] by company, rank by total raised in the period.

    Each result carries the company's latest round in the period (stage, amount,
    investors) plus the period total, which is what "best quarter" is ranked on.
    """
    by_company = {}
    for rec in records:
        if rec.round_date is None or not (start <= rec.round_date <= end):
            continue
        entry = by_company.setdefault(rec.ident, {"raised": 0.0, "latest": rec, "investors": []})
        entry["raised"] += rec.amount_usd or 0.0
        if rec.round_date >= entry["latest"].round_date:
            entry["latest"] = rec
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
            "employee_band": latest.employee_band,
            "stage": latest.stage,
            "last_amount_usd": latest.amount_usd,
            "raised_in_period_usd": e["raised"],
            "backed_by": e["investors"],
            "tracxn_url": latest.tracxn_url,
        })
    return rows


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


def to_markdown(rows: list, label: str) -> str:
    lines = [f"## Top funded companies — {label}", "",
             "| # | Company | Employee band | Stage | Last round | Raised in period | Backed by |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['rank']} | {r['company']} | {r['employee_band'] or '—'} | "
            f"{r['stage'] or '—'} | {_money(r['last_amount_usd'])} | "
            f"{_money(r['raised_in_period_usd'])} | {', '.join(r['backed_by']) or '—'} |")
    return "\n".join(lines) + "\n"
