import json
import unittest
from datetime import date
from pathlib import Path

from collector import leaders
from collector.normalize import employee_band, normalize, parse_amount, parse_date

FIXTURE = Path(__file__).parent / "fixtures" / "sample_apify.json"


class ParsingTest(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(parse_amount("$40M"), 40e6)
        self.assertEqual(parse_amount("USD 12.5 Mn"), 12.5e6)
        self.assertEqual(parse_amount("1.2B"), 1.2e9)
        self.assertEqual(parse_amount("4,000,000"), 4e6)
        self.assertIsNone(parse_amount("undisclosed"))

    def test_parse_date(self):
        self.assertEqual(parse_date("Aug 20, 2026"), date(2026, 8, 20))
        self.assertEqual(parse_date("2026-08-12T10:00:00Z"), date(2026, 8, 12))

    def test_employee_band(self):
        self.assertEqual(employee_band(35), "11-50")
        self.assertEqual(employee_band("51-200"), "51-200")
        self.assertEqual(employee_band(1200), "1001-5000")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.records = normalize(json.loads(FIXTURE.read_text()), since=date(2026, 1, 1))

    def test_filters_rounds_before_start(self):
        names = {r.company for r in self.records}
        self.assertNotIn("Old News", names)
        self.assertEqual(len(self.records), 5)

    def test_last_month_leaders(self):
        label, start, end = leaders.period_bounds("last-month", date(2026, 9, 29))
        self.assertEqual((label, start, end), ("2026-08", date(2026, 8, 1), date(2026, 8, 31)))
        rows = leaders.rank(self.records, start, end)
        self.assertEqual([r["company"] for r in rows], ["Acme Robotics", "Byte Pay", "Quiet Co"])
        top = rows[0]
        self.assertEqual(top["employee_band"], "51-200")
        self.assertEqual(top["stage"], "Series B")
        self.assertEqual(top["last_amount_usd"], 40e6)
        self.assertEqual(top["backed_by"], ["Sequoia", "Accel"])

    def test_last_quarter_sums_rounds(self):
        label, start, end = leaders.period_bounds("last-quarter", date(2026, 9, 29))
        self.assertEqual(label, "2026-Q2")
        rows = leaders.rank(self.records, start, end)
        self.assertEqual(rows[0]["company"], "Cloudnest")

        label, start, end = leaders.period_bounds("last-quarter", date(2026, 10, 5))
        rows = leaders.rank(self.records, start, end)
        byte_pay = next(r for r in rows if r["company"] == "Byte Pay")
        self.assertEqual(byte_pay["raised_in_period_usd"], 15.5e6)  # seed + series A
        self.assertEqual(byte_pay["stage"], "Series A")


if __name__ == "__main__":
    unittest.main()
