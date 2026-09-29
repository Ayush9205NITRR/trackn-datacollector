import unittest
from unittest import mock

from collector import airtable
from collector.schema import FUNDING_FIELDS

TABLES = [{
    "id": "tblyAvdZRaCCgLP94", "name": "Tracxn Database", "primaryFieldId": "fld1",
    "fields": [{"id": "fld1", "name": "Name", "type": "singleLineText"},
               {"id": "fld2", "name": "Notes", "type": "multilineText"},
               {"id": "fld3", "name": "Company", "type": "singleLineText"}],
}]


class EnsureTableTest(unittest.TestCase):
    @mock.patch("collector.airtable.time.sleep")
    @mock.patch("collector.airtable.request_json")
    def test_adds_only_missing_fields_to_existing_table(self, req, _sleep):
        msg = airtable.ensure_table("t", "appX", TABLES, "tblyAvdZRaCCgLP94", FUNDING_FIELDS)
        added = [c.args[3]["name"] for c in req.call_args_list]
        self.assertNotIn("Company", added)
        self.assertIn("Record Key", added)
        self.assertEqual(len(added), len(FUNDING_FIELDS) - 1)
        for c in req.call_args_list:
            self.assertEqual(c.args[:2], ("POST", f"{airtable.API}/meta/bases/appX/tables/"
                                                  "tblyAvdZRaCCgLP94/fields"))
        self.assertIn("Tracxn Database", msg)

    @mock.patch("collector.airtable.request_json")
    def test_creates_missing_table_by_name(self, req):
        airtable.ensure_table("t", "appX", TABLES, "Monthly Leaders", [{"name": "Record Key"}])
        req.assert_called_once_with("POST", f"{airtable.API}/meta/bases/appX/tables", "t",
                                    {"name": "Monthly Leaders", "fields": [{"name": "Record Key"}]})

    def test_unknown_table_id_is_an_error(self):
        with self.assertRaises(RuntimeError):
            airtable.ensure_table("t", "appX", TABLES, "tblNope", FUNDING_FIELDS)


class FillPrimaryTest(unittest.TestCase):
    def test_copies_company_into_foreign_primary(self):
        rows = airtable.fill_primary(TABLES, "tblyAvdZRaCCgLP94", [{"Company": "Acme"}])
        self.assertEqual(rows, [{"Name": "Acme", "Company": "Acme"}])

    def test_leaves_rows_alone_for_new_or_computed_primary(self):
        rows = [{"Record Key": "k", "Company": "Acme"}]
        self.assertEqual(airtable.fill_primary(TABLES, "Monthly Leaders", rows), rows)
        computed = [{**TABLES[0], "fields": [{"id": "fld1", "name": "Name", "type": "formula"}]}]
        self.assertEqual(airtable.fill_primary(computed, "tblyAvdZRaCCgLP94", rows), rows)


class UpsertTest(unittest.TestCase):
    @mock.patch("collector.airtable.time.sleep")
    @mock.patch("collector.airtable.request_json")
    def test_skips_a_rejected_column_and_uploads_the_rest(self, req, _sleep):
        def fake(method, url, token, body):
            if any("LinkedIn URL" in r["fields"] for r in body["records"]):
                raise RuntimeError(f"PATCH {url} -> 403: INVALID_PERMISSIONS")
        req.side_effect = fake
        rows = [{"Record Key": "a", "Company": "A", "LinkedIn URL": "https://x"},
                {"Record Key": "b", "Company": "B"}]
        self.assertEqual(airtable.upsert("t", "appX", "tblY", rows), 2)
        last = req.call_args_list[-1].args[3]["records"]
        self.assertEqual(last, [{"fields": {"Record Key": "a", "Company": "A"}},
                                {"fields": {"Record Key": "b", "Company": "B"}}])

    @mock.patch("collector.airtable.time.sleep")
    @mock.patch("collector.airtable.request_json")
    def test_real_permission_error_still_raises(self, req, _sleep):
        req.side_effect = RuntimeError("PATCH x -> 403: INVALID_PERMISSIONS")
        with self.assertRaises(RuntimeError):
            airtable.upsert("t", "appX", "tblY", [{"Record Key": "a", "Company": "A"}])


class PruneTest(unittest.TestCase):
    @mock.patch("collector.airtable.time.sleep")
    @mock.patch("collector.airtable.request_json")
    def test_deletes_only_stale_keyed_rows(self, req, _sleep):
        req.side_effect = [
            {"records": [{"id": "rec1", "fields": {"Record Key": "keep"}},
                         {"id": "rec2", "fields": {"Record Key": "old"}},
                         {"id": "rec3", "fields": {}}]},  # hand-added row
            None,
        ]
        self.assertEqual(airtable.prune("t", "appX", "tblY", {"keep"}), 1)
        method, url = req.call_args_list[-1].args[:2]
        self.assertEqual(method, "DELETE")
        self.assertTrue(url.endswith("?records%5B%5D=rec2"))


if __name__ == "__main__":
    unittest.main()
