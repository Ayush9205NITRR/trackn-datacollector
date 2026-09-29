# trackn-datacollector

Keeps a research table of startup funding rounds from **Tracxn**, scraped with
**Apify** and stored in **Airtable**, and answers "who had the best month/quarter":
the companies that raised the most, with their employee size band, last funding
amount, funding stage and investors (backed by).

```
companies.txt (Tracxn URLs) ──> Apify: automation-lab/tracxn-company-intelligence-scraper
    ──> normalize (parses Tracxn's funding sentence) ──> data/funding_rounds.json ──> Airtable "Funding Rounds"
                                                            └──> rank last month / quarter ──> reports/*.md + Airtable "Monthly Leaders"
```

Pure Python 3.9+ standard library — no packages to install.

## Setup

1. **Companies to track**: add Tracxn company-profile URLs to `companies.txt`, one
   per line (copy them from the company pages on tracxn.com).
2. **GitHub secrets** (Settings → Secrets and variables → Actions). The Airtable ones
   use the same names as `kylas-airtable-sync`, so paste the same PAT:

   | Secret | Value |
   |---|---|
   | `AIRTABLE_PAT` | Airtable personal access token (scopes `data.records:read/write`, `schema.bases:read/write`, with access to the base) |
   | `AIRTABLE_BASE_ID` | ID of the base for this research table (`app...`) |
   | `APIFY_TOKEN` | Apify API token |

   Optional variables: `START_DATE` (default `2026-01-01`), `APIFY_ACTOR_ID`
   (default `automation-lab/tracxn-company-intelligence-scraper`).
3. Create an empty base in Airtable (e.g. "Tracxn Research"), put its ID in
   `AIRTABLE_BASE_ID`, then run the **Setup Airtable Schema (run once)** workflow
   from the Actions tab. It creates the two tables below.

## Usage

Run the **Tracxn sync** workflow from the Actions tab, or locally:

```bash
export APIFY_TOKEN=apify_api_... AIRTABLE_PAT=pat... AIRTABLE_BASE_ID=app...

python -m collector setup                     # create tables (once)
python -m collector sync --since 2026-01-01   # scrape, save, report, upsert
python -m collector leaders --period last-month
python -m collector leaders --period last-quarter --top 10
```

`sync` merges into the existing snapshot and upserts on `Record Key`
(company + round date + stage), so re-running it never creates duplicates.
Use `--urls other.txt` for a different company list, `--from-file dataset.json`
to load a dataset exported from Apify, and `--no-airtable` to skip the upload.

"Best" = the most money raised in the period (all rounds in the period summed);
stage, last amount and employee band come from the company's latest round in it.

### What the data can and can't show

- The actor reads **public** Tracxn profiles. Funding amount, date and investors
  come from the profile's funding sentence ("Its latest funding round was a
  Series B round on Aug 12, 2026 for $40M ... Its top investors are ..."). Fields
  Tracxn hides for non-premium users come back empty.
- Each scrape sees only a company's **latest** round. The snapshot keeps every
  round it has seen, so history builds up from the first run; a company that
  raised twice between two weekly runs only records the later round, and rounds
  from before the first run (e.g. a February round when the first run is in
  September) are not recovered.
- Companies with no round since `START_DATE` (including unfunded ones) are skipped.

## Keeping it updated

`.github/workflows/sync.yml` runs `sync` every Monday and commits the snapshot and
reports back to the repo.

## Airtable tables

**Funding Rounds** — Record Key, Company, Domain, Tracxn URL, Sector, Location,
Employee Size Band, Funding Stage, Last Funding Amount (USD), Last Funding Date,
Backed By, Total Funding (USD), Month, Quarter, Last Synced.

**Monthly Leaders** — Period, Rank, Company, Employee Size Band, Funding Stage,
Last Funding Amount (USD), Raised In Period (USD), Backed By, Tracxn URL.

## Tests

```bash
python -m unittest discover -s tests -t .
```

> Scraping may be restricted by Tracxn's terms of service. If you have a Tracxn
> subscription with API access, its export can be fed in with `--from-file`.
