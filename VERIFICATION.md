# Verification — 9 October 2026

## Update: on-demand full-year collection

- The daily scheduled collector was replaced by **Collect a YC year**, with one required year input and no company-count cap.
- Eight parallel workers share host-wide pacing. A barrier-based test verifies that company work actually overlaps. JSON checkpoints use a consistent SQLite read transaction.
- All 20 tests pass locally and the initial GitHub Actions test run passed: https://github.com/mallikaakash/yc-people/actions/runs/37968557093.
- Runtime safeguards now mark unprocessed work **incomplete**; valid partial data remains saved and publishable.
- Repository created and pushed: https://github.com/mallikaakash/yc-people. GitHub Pages is configured with workflow-based publishing.
- A full-year remote collection has not yet been run. The dataset remains the earlier three-company profile sample; the company metadata covers the full 2024 directory.

## Earlier local verification

- 18 offline regression tests passed (`python3 -m unittest discover -s tests -v`).
- JavaScript syntax checked with `node --check yc_people/static/app.js`.
- Both GitHub workflow files parsed as valid YAML. They have not been executed on GitHub.
- Live YC import for 2024 discovered 590 listed companies across Winter, Summer and Fall. Profile scanning was intentionally limited to three companies: Abundant, Afternoon.co and Alex. This yielded eight person observations and ten profile links, including two public GitHub organization members.
- Two consecutive runs of `scripts/scheduled_update.py` in a temporary workspace, limited to one company each, restored JSON state correctly and scanned Abundant followed by Afternoon.co. The resulting state contained 590 companies, seven people, and two run records; HTML generation succeeded.
- The generated self-contained HTML was served over local HTTP and checked in a real browser. It displayed the expected counts. Filtering by GitHub returned two records, and opening a member displayed the original organization-membership source and the employment-unverified label.
- Direct `file://` browser navigation could not be tested because the browser tool prohibits that scheme. The exporter embeds all data, CSS, and JavaScript, and snapshot mode performs filtering and CSV generation locally without API requests.
- No GitHub repository was created or modified. No schedule or Pages deployment was activated.

The checked-in HTML is the small live sample, not a completed 590-company profile crawl. The next GitHub run collects the years in `collector.json`; its default is the current year. Public-profile coverage remains partial by design and is described in the README.
