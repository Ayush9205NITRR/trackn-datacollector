"""Create/extend the research tables and upsert records into Airtable.

Tables can be referred to by name or by ID (tbl...). An existing table keeps its
own primary field and any extra columns; `setup` only adds the fields it lacks.
"""

import time
import urllib.parse

from .http import request_json
from .schema import FUNDING_FIELDS, FUNDING_TABLE, LEADERS_FIELDS, LEADERS_TABLE

API = "https://api.airtable.com/v0"
BATCH = 10  # Airtable max records per write request


def create_base(token: str, workspace_id: str, name: str) -> str:
    # First field in each list ("Record Key") becomes the primary field.
    body = {"name": name, "workspaceId": workspace_id,
            "tables": [{"name": FUNDING_TABLE, "fields": FUNDING_FIELDS},
                       {"name": LEADERS_TABLE, "fields": LEADERS_FIELDS}]}
    return request_json("POST", f"{API}/meta/bases", token, body)["id"]


def get_tables(token: str, base_id: str) -> list:
    return request_json("GET", f"{API}/meta/bases/{base_id}/tables", token)["tables"]


def find_table(tables: list, ref: str):
    return next((t for t in tables if ref in (t["id"], t["name"])), None)


def primary_field(table: dict) -> dict:
    return next(f for f in table["fields"] if f["id"] == table["primaryFieldId"])


def ensure_table(token: str, base_id: str, tables: list, ref: str, fields: list) -> str:
    """Create table `ref`, or add missing `fields` to it if it exists. Returns a summary."""
    table = find_table(tables, ref)
    if table is None:
        if ref.startswith("tbl"):
            raise RuntimeError(f"table {ref} not found in base {base_id}")
        request_json("POST", f"{API}/meta/bases/{base_id}/tables", token,
                     {"name": ref, "fields": fields})
        return f"created table '{ref}'"
    have = {f["name"] for f in table["fields"]}
    added = []
    for field in fields:
        if field["name"] not in have:
            request_json("POST", f"{API}/meta/bases/{base_id}/tables/{table['id']}/fields",
                         token, field)
            added.append(field["name"])
            time.sleep(0.25)
    return f"table '{table['name']}' ({table['id']}): added {added or 'no'} fields"


def fill_primary(tables: list, ref: str, rows: list, value_from: str = "Company") -> list:
    """If the table's primary field isn't one we write, copy `value_from` into it
    so rows in an existing table don't show up with a blank name."""
    table = find_table(tables, ref)
    if table is None:
        return rows
    field = primary_field(table)
    primary = field["name"]
    # Formula/autonumber primaries are computed and can't be written.
    if field["type"] not in ("singleLineText", "multilineText") or any(primary in r for r in rows):
        return rows
    return [{primary: r.get(value_from, ""), **r} for r in rows]


def _rejected(exc: Exception) -> bool:
    return any(f"-> {code}" in str(exc) for code in (403, 422))


def upsert(token: str, base_id: str, table: str, rows: list,
           merge_on: str = "Record Key") -> int:
    """Upsert rows (dicts of field values) keyed on `merge_on`.

    If Airtable rejects a batch (403/422), find the column it objects to by
    retrying one field at a time, report it, and upload the rows without it —
    one bad column shouldn't block the whole table. Raises if even the bare
    rows are rejected (a real permission problem).
    """
    url = f"{API}/{base_id}/{urllib.parse.quote(table)}"
    skip = set()

    def send(batch):
        body = {
            "performUpsert": {"fieldsToMergeOn": [merge_on]},
            "records": [{"fields": {k: v for k, v in r.items() if k not in skip}}
                        for r in batch],
            "typecast": True,
        }
        request_json("PATCH", url, token, body)
        time.sleep(0.25)  # stay under 5 requests/second per base

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            send(batch)
        except RuntimeError as exc:
            if not _rejected(exc):
                raise
            bad = _find_bad_field(send, batch[0], merge_on, skip)
            if bad is None:
                raise
            print(f"WARNING: Airtable rejects column {bad!r} in '{table}' "
                  f"({str(exc)[:160]}); uploading without it")
            skip.add(bad)
            send(batch)
    return len(rows)


def _find_bad_field(send, row: dict, merge_on: str, skip: set):
    """Send the row with only the merge key, then add one field at a time; the
    first field that turns an accepted write into a rejection is the culprit."""
    fields = [k for k in row if k != merge_on and k not in skip]
    try:
        send([{merge_on: row[merge_on]}])
    except RuntimeError as exc:
        if _rejected(exc):
            return None  # even the key alone is refused: not a column problem
        raise
    for name in fields:
        try:
            send([{merge_on: row[merge_on], name: row[name]}])
        except RuntimeError as exc:
            if _rejected(exc):
                return name
            raise
    return None


def prune(token: str, base_id: str, table: str, keep: set, key_field: str = "Record Key") -> int:
    """Delete rows this tool wrote earlier (they have a Record Key) whose key is no
    longer in `keep`. Rows without a Record Key — added by hand — are never touched."""
    url = f"{API}/{base_id}/{urllib.parse.quote(table)}"
    stale, offset = [], None
    while True:
        query = f"?pageSize=100&fields%5B%5D={urllib.parse.quote(key_field)}"
        if offset:
            query += f"&offset={offset}"
        page = request_json("GET", url + query, token)
        for rec in page.get("records", []):
            key = rec.get("fields", {}).get(key_field)
            if key and key not in keep:
                stale.append(rec["id"])
        offset = page.get("offset")
        if not offset:
            break
    for i in range(0, len(stale), BATCH):
        ids = "&".join(f"records%5B%5D={r}" for r in stale[i:i + BATCH])
        request_json("DELETE", f"{url}?{ids}", token)
        time.sleep(0.25)
    return len(stale)
