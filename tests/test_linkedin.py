import unittest
from datetime import date

from collector import linkedin
from collector.schema import FundingRecord

SIMA_RESULTS = [
    {"url": "https://www.linkedin.com/company/other-co/", "title": "Other Co | LinkedIn",
     "description": "Other Co. 201-500 employees."},
    {"url": "https://in.linkedin.com/company/sima-ai", "title": "SiMa.ai | LinkedIn",
     "description": "SiMa.ai | 40,112 followers on LinkedIn. Company size: 201-500 employees."},
]


class PickTest(unittest.TestCase):
    def test_picks_matching_company_page_and_size(self):
        hit = linkedin.pick("SiMa.ai", SIMA_RESULTS)
        self.assertEqual(hit["url"], "https://www.linkedin.com/company/sima-ai")
        self.assertEqual(hit["employee_band"], "201-500")

    def test_short_names_need_a_slug_match(self):
        # Seen live: "EMA - Escola do Meio Ambiente" matched "Ema" on title alone.
        wrong = [{"url": "https://br.linkedin.com/company/escoladomeioambiente",
                  "title": "EMA - Escola do Meio Ambiente | LinkedIn"}]
        self.assertEqual(linkedin.pick("Ema", wrong), {})
        right = [{"url": "https://www.linkedin.com/company/ema-unlimited", "title": "Ema"}]
        self.assertEqual(linkedin.pick("Ema", right)["url"],
                         "https://www.linkedin.com/company/ema-unlimited")

    def test_prefers_the_indian_page(self):
        results = [
            {"url": "https://www.linkedin.com/company/everbrands-inc",
             "title": "EverBrands Inc | LinkedIn", "description": "Los Angeles, CA. 11-50 employees"},
            {"url": "https://in.linkedin.com/company/everbrands-india",
             "title": "EverBrands India | LinkedIn",
             "description": "Subway India operator. Mumbai. 1,001-5,000 employees"},
        ]
        hit = linkedin.pick("EverBrands", results)
        self.assertEqual(hit["url"], "https://www.linkedin.com/company/everbrands-india")
        self.assertEqual(hit["employee_band"], "1001-5000")

    def test_no_match_returns_empty(self):
        self.assertEqual(linkedin.pick("Ema", SIMA_RESULTS), {})
        posts = [{"url": "https://www.linkedin.com/posts/ema-raises", "title": "Ema raises"}]
        self.assertEqual(linkedin.pick("Ema", posts), {})

    def test_slug_prefix_match(self):
        results = [{"url": "https://www.linkedin.com/company/balwaan-krishi-pvt-ltd",
                    "title": "Balwaan Krishi Private Limited - LinkedIn"}]
        self.assertTrue(linkedin.pick("Balwaan Krishi", results))

    def test_size_from_snippet(self):
        self.assertEqual(linkedin.size_from_snippet("Employees: 11-50"), "11-50")
        self.assertEqual(linkedin.size_from_snippet("1,001-5,000 employees"), "1001-5000")
        self.assertEqual(linkedin.size_from_snippet("40,112 followers"), "")

    def test_canonical(self):
        self.assertEqual(linkedin.canonical('<a href="https://in.linkedin.com/company/Acme-AI/">'),
                         "https://www.linkedin.com/company/acme-ai")


class EnrichTest(unittest.TestCase):
    def records(self):
        return [FundingRecord("Acme", date(2026, 8, 1), 5e7, domain="acme.ai"),
                FundingRecord("SiMa.ai", date(2026, 9, 28), 1.5e8, employee_band=""),
                FundingRecord("Ghost Co", date(2026, 9, 1), 2e7)]

    def test_website_then_google_then_cache(self):
        searched = []

        def search(token, names):
            searched.append(list(names))
            return {"SiMa.ai": SIMA_RESULTS}

        cache, today = {}, date(2026, 9, 29)
        recs = self.records()
        stats = linkedin.enrich(recs, cache, token="t", today=today, search=search,
                                website=lambda d: "https://www.linkedin.com/company/acme")
        self.assertEqual(stats, {"website": 1, "google": 1, "missed": 1})
        self.assertEqual(searched, [["SiMa.ai", "Ghost Co"]])  # Acme found via website
        self.assertEqual(recs[0].linkedin_url, "https://www.linkedin.com/company/acme")
        self.assertEqual(recs[1].linkedin_url, "https://www.linkedin.com/company/sima-ai")
        self.assertEqual(recs[1].employee_band, "201-500")  # filled from the snippet
        self.assertEqual(recs[2].linkedin_url, "")

        # Next run: everything cached, the miss isn't retried until RETRY_DAYS pass.
        searched.clear()
        recs = self.records()
        linkedin.enrich(recs, cache, token="t", today=today, search=search,
                        website=lambda d: "")
        self.assertEqual(searched, [])
        self.assertEqual(recs[1].linkedin_url, "https://www.linkedin.com/company/sima-ai")
        linkedin.enrich(self.records(), cache, token="t", today=date(2026, 11, 1),
                        search=search, website=lambda d: "")
        self.assertEqual(searched, [["Ghost Co"]])

    def test_search_failure_does_not_raise(self):
        def boom(token, names):
            raise RuntimeError("actor down")
        cache = {}
        linkedin.enrich(self.records(), cache, token="t", today=date(2026, 9, 29),
                        search=boom, website=lambda d: "")
        self.assertNotIn("simaai", cache)  # not marked as checked, so retried next run


if __name__ == "__main__":
    unittest.main()
