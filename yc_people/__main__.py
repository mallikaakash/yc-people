import argparse
from pathlib import Path

from .database import Database
from .pipeline import run_import, validate_year


def main():
    parser = argparse.ArgumentParser(description="YC companies and source-backed public profiles")
    parser.add_argument("--db", default="data/yc_people.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("import-year")
    scan.add_argument("year", type=validate_year)
    scan.add_argument("--limit", type=int, default=0, help="Limit profile scans; company discovery always includes all batches")
    scan.add_argument("--refresh", action="store_true")
    scan.add_argument("--founders-only", action="store_true")
    scan.add_argument("--workers", type=int, default=8)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    export = commands.add_parser("export")
    export.add_argument("--year", type=int)
    export.add_argument("--out", default="exports/yc-people.csv")
    html_export = commands.add_parser("export-html")
    html_export.add_argument("--out", default="docs/index.html")
    args = parser.parse_args()
    db = Database(args.db)
    try:
        if args.command == "import-year":
            run_import(db, args.year, args.limit, args.refresh, not args.founders_only, workers=args.workers)
        elif args.command == "serve":
            from .server import serve
            serve(db, args.port)
        elif args.command == "export":
            path = Path(args.out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(db.export_csv({"year": args.year}), encoding="utf-8-sig")
            print(path.resolve())
        elif args.command == "export-html":
            from .static_export import export_html
            print(export_html(db, args.out).resolve())
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
