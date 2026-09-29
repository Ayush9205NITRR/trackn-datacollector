import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from collector import apify, leaders, sources
from collector.normalize import (employee_band, is_series_a_plus, normalize, parse_amount,
                                 parse_date, parse_funding_summary)
from collector.schema import IPO, MNA, company_ident, fiscal_quarter

FIXTURE = Path(__file__).parent / "fixtures" / "sample_apify.json"
FY27_H1 = (date(2026, 4, 1), date(2026, 9, 30))


class ParsingTest(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(parse_amount("$40M"), 40e6)
        self.assertEqual(parse_amount("USD 12.5 Mn"), 12.5e6)
        self.assertEqual(parse_amount("1.2B"), 1.2e9)
        self.assertEqual(parse_amount("4,000,000"), 4e6)
        self.assertEqual(parse_amount("Rs 880 Cr"), 100e6)  # at 88 INR/USD
        self.assertEqual(parse_amount("₹44 crore"), 5e6)
        self.assertIsNone(parse_amount("undisclosed"))

    def test_parse_date(self):
        self.assertEqual(parse_date("Aug 20, 2026"), date(2026, 8, 20))
        self.assertEqual(parse_date("2026-08-12T10:00:00Z"), date(2026, 8, 12))

    def test_employee_band(self):
        self.assertEqual(employee_band(35), "11-50")
        self.assertEqual(employee_band("51-200"), "51-200")
        self.assertEqual(employee_band(1200), "1001-5000")

    def test_series_a_plus(self):
        for stage in ("Series A", "Series B2", "series c extension", "Growth", "Pre-IPO",
                      "Private Equity"):
            self.assertTrue(is_series_a_plus(stage), stage)
        for stage in ("Seed", "Pre-Series A", "Angel", "Venture Debt", "IPO", "Grant", ""):
            self.assertFalse(is_series_a_plus(stage), stage)

    def test_company_ident_and_fiscal_quarter(self):
        self.assertEqual(company_ident("Acme Robotics Pvt. Ltd."), company_ident("Acme Robotics"))
        self.assertEqual(fiscal_quarter(date(2026, 4, 1)), "FY27 Q1")
        self.assertEqual(fiscal_quarter(date(2026, 9, 30)), "FY27 Q2")
        self.assertEqual(fiscal_quarter(date(2027, 1, 15)), "FY27 Q4")


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


class SourcesTest(unittest.TestCase):
    def test_fill_placeholders(self):
        since = date(2026, 4, 1)
        got = sources.fill({"from": "{since}", "to": "{until}", "days": "{days_back}",
                            "n": 5, "list": ["{since}"]}, since, date(2026, 9, 30))
        self.assertEqual(got["from"], "2026-04-01")
        self.assertEqual(got["to"], "2026-09-30")
        self.assertIsInstance(got["days"], int)
        self.assertEqual(got["list"], ["2026-04-01"])
        self.assertEqual(got["n"], 5)

    def test_load_urls(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# comment\nhttps://tracxn.com/d/companies/a/__1?utm=x\n\n"
                    "https://tracxn.com/d/companies/a/__1/  # dup\n")
        self.assertEqual(apify.load_urls(f.name), ["https://tracxn.com/d/companies/a/__1"])
        self.assertEqual(apify.build_input(["u"]), {"startUrls": [{"url": "u"}]})

    def test_rejects_non_tracxn(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("https://example.com/foo\n")
        with self.assertRaises(ValueError):
            apify.load_urls(f.name)

    def test_repo_sources_config_is_valid(self):
        cfg = sources.load(Path(__file__).parent.parent / "sources.json")
        self.assertTrue(cfg)
        for s in cfg:
            self.assertIn("actor", s)
            self.assertTrue("input" in s or "urls_file" in s)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        items = json.loads(FIXTURE.read_text())
        self.records = normalize(items, since=FY27_H1[0], until=FY27_H1[1], country="India",
                                 min_unlabeled_usd=10e6)
        self.by_name = {r.company: r for r in self.records}

    def test_keeps_only_series_a_plus_and_mna_in_period_and_country(self):
        self.assertEqual(sorted(self.by_name), sorted([
            "Acme Robotics Pvt Ltd", "Byte Pay", "Cloudnest", "PayZen", "Blinkit", "Lernify",
            "Simaai", "EverBrands", "NSE", "Balwaan Krishi"]))
        # Seed (Byte Pay's), Pre-Series A (Kiddo), pre-FY (Old News), US (Globex),
        # non-deal news (Swiggy CFO), stake sales (Mastercard), rights issues (Ola),
        # VC fund closes (WEH Ventures) and failed scrapes are all dropped.

    def test_ipos_and_post_dates(self):
        ever, nse = self.by_name["EverBrands"], self.by_name["NSE"]
        self.assertEqual((ever.deal_type, ever.amount_usd), (IPO, 72.29e6))
        self.assertEqual(ever.post_date, date(2026, 9, 29))
        self.assertEqual(ever.source_url, "https://entrackr.com/news/everbrands")
        self.assertEqual(nse.deal_type, IPO)
        self.assertIsNone(nse.amount_usd)  # "list at Rs 1,800" is a share price, not a size
        # RSS-style date from Inc42 becomes the post date
        self.assertEqual(self.by_name["Balwaan Krishi"].post_date, date(2026, 9, 28))

    def test_merges_same_deal_from_two_sources(self):
        acme = self.by_name["Acme Robotics Pvt Ltd"]
        self.assertEqual(acme.investors, ["Sequoia", "Accel", "Y Combinator"])
        self.assertEqual(acme.employee_band, "51-200")
        self.assertEqual(acme.source_url, "https://inc42.com/buzz/acme")
        self.assertEqual(acme.source, "datahyena, inc42-yourstory")

    def test_news_amount_in_millions_and_descriptor_name(self):
        sima = self.by_name["Simaai"]
        self.assertEqual((sima.amount_usd, sima.stage), (150e6, "Series C"))

    def test_mna_from_headlines(self):
        blinkit, lernify = self.by_name["Blinkit"], self.by_name["Lernify"]
        self.assertEqual((blinkit.deal_type, blinkit.acquirer), (MNA, "Zomato"))
        self.assertEqual(blinkit.amount_usd, 568e6)
        self.assertEqual((lernify.deal_type, lernify.acquirer), (MNA, "upGrad"))
        self.assertEqual(self.by_name["PayZen"].investors, ["Peak XV"])

    def test_fy27_h1_leaders(self):
        label, start, end = leaders.custom_bounds(*FY27_H1)
        self.assertEqual(label, "FY27 Q1–Q2")
        rows = leaders.rank(self.records, start, end)
        self.assertEqual([r["company"] for r in rows],
                         ["Simaai", "Cloudnest", "Acme Robotics Pvt Ltd", "PayZen", "Byte Pay",
                          "Balwaan Krishi"])
        acme = rows[2]
        self.assertEqual((acme["employee_band"], acme["stage"], acme["last_amount_usd"]),
                         ("51-200", "Series B", 40e6))
        self.assertEqual(acme["backed_by"], ["Sequoia", "Accel", "Y Combinator"])
        deals = leaders.mna(self.records, start, end)
        self.assertEqual([d.company for d in deals], ["Lernify", "Blinkit"])
        ipo_deals = leaders.ipos(self.records, start, end)
        self.assertEqual([d.company for d in ipo_deals], ["EverBrands", "NSE"])
        md = leaders.to_markdown(rows, label, deals, ipo_deals)
        self.assertIn("## M&A — FY27 Q1–Q2", md)
        self.assertIn("| Blinkit | Zomato | $568.0M |", md)
        self.assertIn("## IPOs — FY27 Q1–Q2", md)
        self.assertIn("| 2026-09-29 | EverBrands | $72.3M | 2026-09-29 | "
                      "[entrackr](https://entrackr.com/news/everbrands) |", md)

    def test_periods(self):
        today = date(2026, 9, 29)
        self.assertEqual(leaders.period_bounds("last-month", today),
                         ("2026-08", date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual(leaders.period_bounds("last-quarter", today),
                         ("FY27 Q1", date(2026, 4, 1), date(2026, 6, 30)))
        self.assertEqual(leaders.period_bounds("fy-to-date", today),
                         ("FY27 YTD", date(2026, 4, 1), today))
        self.assertEqual(leaders.period_bounds("fy-to-date", date(2027, 2, 1))[1],
                         date(2026, 4, 1))

    def test_all_stages_mode_keeps_seed(self):
        items = json.loads(FIXTURE.read_text())
        records = normalize(items, since=FY27_H1[0], until=FY27_H1[1], country="India")
        self.assertIn("Kiddo", {r.company for r in records})


if __name__ == "__main__":
    unittest.main()
