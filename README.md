# trackn-datacollector

Keeps a research table of startup funding rounds from **Tracxn**, scraped with
**Apify** and stored in **Airtable**, and answers "who had the best month/quarter":
the companies that raised the most, with their employee size band, last funding
amount, funding stage and investors (backed by).

```
Apify actor (Tracxn scrape) ──> normalize ──> data/funding_rounds.json ──> Airtable "Funding Rounds"
                                                   └──> rank last month / quarter ──> reports/*.md + Airtable "Monthly Leaders"
```

Pure Python 3.9+ standard library — no packages to install.

## Setup

1. **Apify**: pick a Tracxn scraper actor in the Apify Store (or your own) and note
   its ID (`username~actor-name`). Edit `apify_input.json` so it matches that actor's
   input schema; `{since}` and `{until}` are replaced with the date range.
   If the actor's output field names differ from the ones expected, add them to
   `FIELD_ALIASES` in `collector/normalize.py`.
2. **Airtable**: create a personal access token with scopes
   `data.records:read`, `data.records:write`, `schema.bases:read`, `schema.bases:write`
   and access to your workspace.
3. Create the base and tables:

   ```bash
   export AIRTABLE_TOKEN=pat...
   export AIRTABLE_WORKSPACE_ID=wsp...          # to create a new base, or
   export AIRTABLE_BASE_ID=app...               # to add the tables to an existing base
   python -m collector setup
   ```

## Usage

```bash
export APIFY_TOKEN=apify_api_... APIFY_ACTOR_ID=username~tracxn-scraper
export AIRTABLE_TOKEN=pat... AIRTABLE_BASE_ID=app...

# Backfill everything since January, write reports, upsert into Airtable
python -m collector sync --since 2026-01-01

# Who had the best last month / last quarter (from the local snapshot)
python -m collector leaders --period last-month
python -m collector leaders --period last-quarter --top 10
```

`sync` merges into the existing snapshot and upserts on `Record Key`
(company + round date + stage), so re-running it never creates duplicates.
Use `--from-file dataset.json` to load a dataset you exported from Apify
instead of starting a new run, and `--no-airtable` to skip the upload.

"Best" = the most money raised in the period (all rounds in the period summed);
stage, last amount and employee band come from the company's latest round in it.

## Keeping it updated

`.github/workflows/sync.yml` runs `sync` every Monday and commits the snapshot and
reports. Configure in the GitHub repo settings:

- Secrets: `APIFY_TOKEN`, `AIRTABLE_TOKEN`
- Variables: `APIFY_ACTOR_ID`, `AIRTABLE_BASE_ID`, `START_DATE` (e.g. `2026-01-01`)

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
