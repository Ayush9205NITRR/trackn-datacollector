# trackn-datacollector

Finds **every Indian company that raised Series A or later, went through M&A, or
filed for / completed an IPO**,
from the start of the financial year (FY27 = from 1 Apr 2026), keeps them in the
**Tracxn Database** Airtable table, and answers "who had the best quarter":
the companies that raised the most, with employee size band, last funding amount,
funding stage and investors (backed by).

No company list needed: deals are discovered from Apify actors.

```
sources.json ──> Apify actors ──────────────────────────────┐
  datahyena/company-funding-rounds   (funding-rounds database: backfill)
  nexgendata/india-startup-funding-tracker  (Inc42 + YourStory news)
  nesora/india-startup-funding-tracker      (Entrackr news: funding, M&A, IPO)
                                                             ▼
normalize ─> keep Series A+, M&A & IPO, India, since 1 Apr ─> merge duplicates across sources
    ─> data/funding_rounds.json ─> Airtable "Tracxn Database"
    └─> rank last month / last FY quarter / FY to date ─> reports/*.md + Airtable "Monthly Leaders"
```

Pure Python 3.9+ standard library — no packages to install.

## Setup

1. **GitHub secrets** (Settings → Secrets and variables → Actions). The Airtable
   token uses the same name as `kylas-airtable-sync`, so paste the same PAT:

   | Secret | Value |
   |---|---|
   | `AIRTABLE_PAT` | Airtable personal access token (scopes `data.records:read/write`, `schema.bases:read/write`, with access to the Tracxn Database base) |
   | `APIFY_TOKEN` | Apify API token |

2. **Check the actor inputs** in `sources.json`. Open each actor's *Input* tab on
   Apify and make sure the field names match (e.g. the date-range and country
   filters for datahyena). Wrong names usually don't fail — the actor just ignores
   them — and the collector filters by date, country and stage itself anyway, but
   the right filters make runs cheaper and more complete. Set `"enabled": false`
   to switch a source off.

3. **Airtable**: the workflows write to the **Tracxn Database** base
   (`appQQ97d3jA6bwwb6`), deals into a table named **Deals**, and create a
   `Monthly Leaders` table next to it. Run the **Setup Airtable Schema (run once)**
   workflow from the Actions tab: it adds the missing columns to the existing table
   (its own columns and primary field are left as they are; sync fills the primary
   field with the company name) and creates `Monthly Leaders`. Re-run it after
   upgrading — new columns (Deal Type, Acquirer, Country, Source, Source URL) are
   added the same way.

4. Run the **Tracxn sync** workflow. It then runs every Monday.

Optional repository variables: `START_DATE` (default `2026-04-01`), `COUNTRY`
(default `India`), `MIN_UNLABELED_USD`, `INR_PER_USD`, `AIRTABLE_BASE_ID`,
`AIRTABLE_FUNDING_TABLE`, `AIRTABLE_LEADERS_TABLE`.

## What gets kept

- **Funding**: Series A, B, C… (incl. extensions like "Series B2"), growth, private
  equity, pre-IPO. Dropped: seed, angel, pre-Series A, debt, grants, IPOs.
  Rounds a source doesn't label are kept only if at least $10M (`MIN_UNLABELED_USD`).
  `sync --all-stages` keeps everything.
- **M&A**: deals a source marks as acquisitions/mergers, and news headlines like
  "Zomato acquires quick commerce startup Blinkit for $568 Mn" or "X acquired by Y"
  (target = the company, acquirer in its own column).
- **IPO**: DRHP filings, IPO openings and listings (issue size as the amount).
  Pre-IPO rounds count as funding. Dropped: stake sales/exits, rights issues,
  VC fund closes.
- **Post Date**: when the news article was published (the earliest one when
  several sources report the same deal), next to the deal's own date.
- Only deals dated from `START_DATE`, in `COUNTRY` when the source reports a country.
- The same deal from several sources (same company, round and month) is merged
  into one row: investors are combined, and gaps (employees, domain, link) are
  filled from whichever source has them. Amounts in rupees are converted to USD.

## LinkedIn pages

Each sync finds the company's LinkedIn page (**LinkedIn URL** column, and company
names in the reports link to it), without scraping LinkedIn itself:

1. **Company website** — when a source gives the domain, the homepage's
   `linkedin.com/company/...` link (usually in the footer).
2. **Google search** via Apify's `apify/google-search-scraper`:
   `"Company" site:linkedin.com/company`, keeping only a result whose page title
   or URL matches the company name. When the result snippet states the company
   size ("201-500 employees"), it also fills a missing **Employee Size Band**.

Lookups are cached in `data/linkedin.json`, so each company is searched once
(one Google query per new company); misses are retried after 30 days. To fix a
wrong match, edit its entry there. `LINKEDIN_LOOKUP=off` turns this off.

## Reports

`sync` writes to `reports/` and to the Monthly Leaders table:

- `leaders_<YYYY-MM>.md` — last month
- `leaders_FY27_Q1.md` — last completed FY quarter (FY27 Q1 = Apr–Jun, Q2 = Jul–Sep)
- `leaders_FY27_YTD.md` — the financial year so far (= Q1–Q2 FY27 at the end of September)

Each lists the top funded companies (ranked by total raised in the period), then
the M&A deals and IPOs in it, each with its post date and source link. Any range on demand:

```bash
python -m collector leaders --from 2026-04-01 --to 2026-09-30   # FY27 Q1–Q2
python -m collector leaders --period last-quarter --top 10
```

## Running locally

```bash
export APIFY_TOKEN=apify_api_... AIRTABLE_PAT=pat...
export AIRTABLE_BASE_ID=appQQ97d3jA6bwwb6 AIRTABLE_FUNDING_TABLE=Deals

python -m collector setup                     # add columns / tables (once)
python -m collector sync                      # discover, save, report, upsert
python -m collector sync --from-file dataset.json --no-airtable   # offline test
```

`sync` merges into the existing snapshot and upserts on `Record Key`
(company | round | month), so re-running never creates duplicates.

## Limits

- **News sources only reach back a few weeks** (they read RSS feeds, ~20–100
  latest stories). The April–September backfill depends on the datahyena
  database, which is charged per record: **free Apify accounts get a one-time
  50-record sample**, so the backfill needs a paid Apify plan. Weekly runs catch
  new deals from the news sources either way, and the history builds up.
- Run `python -m collector inspect` (or the *Inspect Apify actors* workflow) to
  print each actor's real input fields and its last run log.
- Coverage is what these sources report — deals no one wrote about are missed,
  and headline-only M&A items may lack amount and employee size.
- **Employee size band** comes from sources that carry headcount (datahyena).
  Companies only seen in news have it blank; to fill those, add their Tracxn URLs
  to `companies.txt` and enable the `tracxn-profiles` source.

## Airtable tables

**Tracxn Database** (deals) — Record Key, Company, Deal Type, Domain, LinkedIn URL, Tracxn URL,
Sector, Location, Country, Employee Size Band, Funding Stage, Last Funding Amount
(USD), Last Funding Date, Post Date, Backed By, Acquirer, Total Funding (USD), Month, Quarter
(FY), Source, Source URL, Last Synced.

**Monthly Leaders** — Period, Rank, Company, Employee Size Band, Funding Stage,
Last Funding Amount (USD), Raised In Period (USD), Last Funding Date, Post Date,
Backed By, LinkedIn URL, Tracxn URL.

## Tests

```bash
python -m unittest discover -s tests -t .
```
