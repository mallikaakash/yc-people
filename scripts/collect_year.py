"""One requested year, full collection, persistent repo data, and static output."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yc_people.database import Database
from yc_people.pipeline import run_import, validate_year
from yc_people.static_export import export_html, restore_state, save_state, export_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("year", nargs="?", default=os.getenv("YC_YEAR"), type=validate_year)
    parser.add_argument("--refresh", action="store_true", default=os.getenv("YC_REFRESH", "false").lower() == "true")
    args = parser.parse_args()
    if args.year is None:
        parser.error("A single year is required, e.g. python scripts/collect_year.py 2024")
    year = validate_year(args.year)
    config = json.loads(Path("collector.json").read_text())
    workers, budget = int(config["workers"]), int(config["max_runtime_seconds"])
    if not 1 <= workers <= 12 or not 60 <= budget <= 9000:
        raise ValueError("Invalid worker count or runtime safeguard")
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "directory.sqlite3")
        restore_state(db, "state/directory.json")
        try:
            run_id = run_import(db, year, refresh=args.refresh, workers=workers, max_seconds=budget,
                                checkpoint=lambda: save_state(db, "state/directory.json"))
            with db.connect() as connection:
                run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run["failed"] or run["status"] == "incomplete":
                failures.append(run["message"])
        except Exception as error:
            failures.append(str(error))
        finally:
            save_state(db, "state/directory.json")
        export_html(db, "docs/index.html")
        export_directory(db)
        Path("docs/.nojekyll").touch()
        Path("docs/yc-people.csv").write_text(db.export_csv(), encoding="utf-8-sig")
        data = db.snapshot({"year": year})
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(f"## YC {year}\n\n{len(data['companies'])} companies · {len(data['people'])} person observations.\n\nAll companies were selected for scanning. Source failures and incomplete work remain visible in the directory.\n\nDownload **yc-directory**, visit GitHub Pages, or open **DIRECTORY.md** in the repository.\n")
        if os.getenv("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write("ready=true\n")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
