"""Create the research base/tables and upsert records into Airtable."""

import time
import urllib.parse

from .http import request_json
from .schema import FUNDING_FIELDS, FUNDING_TABLE, LEADERS_FIELDS, LEADERS_TABLE

API = "https://api.airtable.com/v0"
BATCH = 10  # Airtable max records per write request


def _tables(fields_by_table: dict) -> list:
    # First field in each list ("Record Key") becomes the primary field.
    return [{"name": name, "fields": fields} for name, fields in fields_by_table.items()]


def create_base(token: str, workspace_id: str, name: str) -> str:
    body = {"name": name, "workspaceId": workspace_id,
            "tables": _tables({FUNDING_TABLE: FUNDING_FIELDS,
                               LEADERS_TABLE: LEADERS_FIELDS})}
    return request_json("POST", f"{API}/meta/bases", token, body)["id"]


def ensure_tables(token: str, base_id: str) -> list:
    """Create any missing tables in an existing base. Returns names created."""
    existing = request_json("GET", f"{API}/meta/bases/{base_id}/tables", token)
    names = {t["name"] for t in existing["tables"]}
    created = []
    for name, fields in ((FUNDING_TABLE, FUNDING_FIELDS), (LEADERS_TABLE, LEADERS_FIELDS)):
        if name not in names:
            request_json("POST", f"{API}/meta/bases/{base_id}/tables", token,
                         {"name": name, "fields": fields})
            created.append(name)
    return created


def upsert(token: str, base_id: str, table: str, rows: list,
           merge_on: str = "Record Key") -> int:
    """Upsert rows (dicts of field values) keyed on `merge_on`."""
    url = f"{API}/{base_id}/{urllib.parse.quote(table)}"
    for i in range(0, len(rows), BATCH):
        body = {
            "performUpsert": {"fieldsToMergeOn": [merge_on]},
            "records": [{"fields": r} for r in rows[i:i + BATCH]],
            "typecast": True,
        }
        request_json("PATCH", url, token, body)
        time.sleep(0.25)  # stay under 5 requests/second per base
    return len(rows)
