import json
import unittest
from datetime import date
from pathlib import Path

from collector import apify, leaders
from collector.normalize import (employee_band, normalize, parse_amount, parse_date,
                                 parse_funding_summary)

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


class FundingSummaryTest(unittest.TestCase):
    def test_full_sentence(self):
        out = parse_funding_summary(
            "Zepto has raised a total funding of $1.95B over 10 rounds. Its first funding "
            "round was on Jul 01, 2021. Its latest funding round was a Series G round on "
            "Aug 30, 2024 for $340M. 58 investors participated in its funding rounds. "
            "Its top investors are Nexus Venture Partners, Glade Brook Capital and 2 others.")
        self.assertEqual(out["totalFunding"], "1.95B")
        self.assertEqual(out["latestFundingRound"], "Series G")
        self.assertEqual(out["latestFundingDate"], "Aug 30, 2024")
        self.assertEqual(out["latestFundingAmount"], "340M")
        self.assertEqual(out["investors"], ["Nexus Venture Partners", "Glade Brook Capital"])

    def test_unfunded(self):
        self.assertEqual(parse_funding_summary("Amatik has not raised any funding rounds yet."), {})


class UrlListTest(unittest.TestCase):
    def test_load_urls(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# comment\nhttps://tracxn.com/d/companies/a/__1?utm=x\n\n"
                    "https://tracxn.com/d/companies/a/__1/  # dup\n")
        self.assertEqual(apify.load_urls(f.name), ["https://tracxn.com/d/companies/a/__1"])
        self.assertEqual(apify.build_input(["u"]), {"startUrls": [{"url": "u"}]})

    def test_rejects_non_tracxn(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("https://example.com/foo\n")
        with self.assertRaises(ValueError):
            apify.load_urls(f.name)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.records = normalize(json.loads(FIXTURE.read_text()), since=date(2026, 1, 1))

    def test_filters_rounds_before_start(self):
        names = {r.company for r in self.records}
        self.assertNotIn("Old News", names)
        self.assertNotIn("Amatik", names)  # unfunded: no round to record
        self.assertEqual(len(self.records), 5)

    def test_last_month_leaders(self):
        label, start, end = leaders.period_bounds("last-month", date(2026, 9, 29))
        self.assertEqual((label, start, end), ("2026-08", date(2026, 8, 1), date(2026, 8, 31)))
        rows = leaders.rank(self.records, start, end)
        self.assertEqual([r["company"] for r in rows], ["Acme Robotics", "Byte Pay", "Quiet Co"])
        top = rows[0]
        self.assertEqual(top["employee_band"], "51-200")
        self.assertEqual(top["tracxn_url"], "https://tracxn.com/d/companies/acme-robotics/__acme1")
        self.assertEqual(top["stage"], "Series B")
        self.assertEqual(top["last_amount_usd"], 40e6)
        self.assertEqual(top["backed_by"], ["Sequoia", "Accel", "Y Combinator"])

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
