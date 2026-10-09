# YC People

**Enter one year → collect every publicly listed YC company in that year's batches → discover public people profiles → save the results in this repository → view them on GitHub Pages.**

[Open the directory](https://mallikaakash.github.io/yc-people/) · [Run a year](https://github.com/mallikaakash/yc-people/actions/workflows/collect.yml) · [Repository directory](DIRECTORY.md)

## Use it on GitHub

1. Open **Actions → Collect a YC year → Run workflow**.
2. Enter a single year, such as **2024**, and click **Run workflow**.
3. The workflow discovers all public batches for that year, saves every listed company, and scans their public people/profile sources with **eight parallel workers**.
4. It commits the resulting data and HTML to the repository, uploads a downloadable directory, and publishes GitHub Pages when enabled.

There is **no daily schedule and no 25-company cap**. A single invocation attempts the entire selected year. Profile requests remain paced per host, and slow or unavailable websites can take time. Repeating a year skips previously scanned companies and retries failed YC-page scans. Enable **Refresh** to also recheck saved companies and optional sources with warnings.

The **Run a year** button on the generated page opens GitHub's workflow form. Starting a collection requires repository write access; the public static page itself does not receive a GitHub token. GitHub's [manual workflow documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow) covers this trigger.

You can also trigger it from an authenticated terminal:

```bash
gh workflow run collect.yml --repo mallikaakash/yc-people -f year=2024
gh run watch --repo mallikaakash/yc-people
```

## What is saved

| File | Purpose |
| --- | --- |
| `state/directory.json` | Durable database state, including sources, timestamps and run history |
| `docs/index.html` | Searchable self-contained HTML directory |
| `docs/companies.json` | Company metadata and scan status |
| `docs/people.json` | People, profile links and supporting evidence |
| `docs/yc-people.csv` | Spreadsheet-friendly export |
| `DIRECTORY.md` | Company table rendered directly by GitHub |

Each new year is added to the existing database. The workflow restores JSON into temporary SQLite, performs the collection, and saves JSON back into Git. It checkpoints after discovery and every ten completed company scans; no database service is required.

The HTML embeds its data, styling and JavaScript, so it can also be downloaded and opened on its own. Search, filters, evidence dialogs and CSV export run in the browser. Changing an HTML filter searches collected data; it does not start a new collection.

## Data sources and limits

| Source | What it provides | How people are labeled |
| --- | --- | --- |
| YC public directory | All public batches and companies for the requested year, description, industry, website, reported team size | Company metadata |
| YC company pages | Founders, roles and published LinkedIn, GitHub, X or website links | YC founder |
| Company team/about pages | Structured Person records and explicitly named personal-profile links | Company-listed; review affiliation |
| GitHub organizations linked by YC/company websites | Public members and GitHub handles | GitHub member; employment unverified |

The year is the company's **YC batch year**, not a person's employment year. All season names are discovered dynamically, including Spring and Fall where present. Company pagination is checked against the source count; incomplete enumeration fails visibly.

**All public companies does not mean all employees.** Founder information may be historical; company-listed people may include advisers or alumni; GitHub membership does not establish employment. Team size is not a profile coverage count. Each observation retains source URLs and first/last seen timestamps.

Only published profile links are stored. LinkedIn URLs are collected from the above sources; LinkedIn employee lists are not scraped. There is no email guessing, login scraping, automated messaging, or paid enrichment service.

Website discovery reads at most three same-host team/about pages linked from the homepage. It supports structured Person data and directly named links; icon-only team cards and JavaScript-rendered rosters are not supported. GitHub discovery checks at most two linked organizations and 30 public members per organization; capped results receive a source note. A linked organization may belong to a project/partner, so inspect the evidence.

Fetches use robots.txt checks for company sites, host-wide request pacing across workers, bounded retries and a 4 MB response limit. Failed sources are recorded. A source error does not erase previously collected people. Profiles that disappear remain stored; last-seen dates help assess staleness. Matching uses source IDs and exact canonical profile URLs within a company, not names across companies.

## GitHub setup for another repository

1. Push the project to the repository's default branch. Update the links at the top of this README if using a different repository.
2. Repository policies must permit the workflow's `contents: write` permission and bot commits to the default branch. GitHub supplies its built-in token; no external API keys are needed.
3. For hosting, set **Settings → Pages → Source: GitHub Actions**. Add the Actions repository variable **`PUBLISH_PAGES=true`**.
4. Run **Collect a YC year** with the desired year.

The workflow deploys a Pages artifact explicitly, rather than relying on a bot commit to trigger a separate Pages build. See [GitHub Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

A public repository exposes its committed directory and state; Pages exposes the generated profiles to its configured audience. GitHub plan quotas apply. No paid data provider is used.

`collector.json` controls concurrency (default eight workers, maximum twelve) and a 150-minute runtime safeguard. There is no company-count cap. If the safeguard is reached, pending companies remain pending and the run reports **incomplete**; rerun the same year to resume. Failed companies likewise remain retryable. Valid partial results are saved and published with their status while the collection job reports failures. A forcibly killed runner may lose changes not yet committed; earlier committed state remains intact.

## Run locally

Python **3.10+**, macOS or Linux. Standard library only; no packages or build step.

```bash
# Same full-year collector used by GitHub Actions; saves JSON and HTML in the repo
python3 scripts/collect_year.py 2024

# Refresh all sources for that year
python3 scripts/collect_year.py 2024 --refresh

# Optional local dashboard with its own SQLite database
python3 -m yc_people serve

# Full-year collection in the local SQLite database
python3 -m yc_people import-year 2024 --workers 8

# Optional small diagnostic run
python3 -m yc_people import-year 2024 --limit 10

# Generate self-contained HTML from the local SQLite database
python3 -m yc_people export-html --out docs/index.html

# CSV export
python3 -m yc_people export --year 2024 --out exports/yc-2024.csv

# Offline regression tests
python3 -m unittest discover -s tests -v
```

The local dashboard runs at **http://127.0.0.1:8765** and stores data in `data/yc_people.sqlite3`. It is a loopback-only research app, not an authenticated internet service. Its SQLite database is separate from the repository collector's JSON state. The local dashboard defaults to scanning the whole year, with smaller limits available for diagnostics.

For larger local GitHub imports you can optionally set `GITHUB_TOKEN` in the shell for API quota. It stays in the process and is sent only to GitHub's API; it is not saved in the database or HTML. GitHub Actions already supplies its own token. Private organization membership is never requested.

## Reference

Inspired by [adityajha2005/yc-outreach](https://github.com/adityajha2005/yc-outreach) and the persistence/publishing pattern in [rss-telegram-tracker](https://github.com/mallikaakash/rss-telegram-tracker). This implementation focuses on a persistent, source-backed research directory.

YC's public-page search integration is undocumented and may require maintenance. GitHub adapter reference: [public organization members](https://docs.github.com/en/rest/orgs/members#list-public-organization-members).
