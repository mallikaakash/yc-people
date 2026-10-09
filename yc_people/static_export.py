import json
import os
import re
from pathlib import Path

from .database import now


def export_html(db, path="docs/index.html"):
    """One file, including data/styles/JS. Works under file:// and GitHub Pages."""
    root = Path(__file__).parent / "static"
    snapshot = db.snapshot()
    snapshot.pop("database", None)
    snapshot["generated_at"] = now()
    repository = os.getenv("GITHUB_REPOSITORY", "")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        snapshot["workflow_url"] = f"https://github.com/{repository}/actions/workflows/collect.yml"
    embedded = json.dumps(snapshot, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = (root / "index.html").read_text()
    page = page.replace('<link rel="stylesheet" href="/style.css">', '<style>' + (root / "style.css").read_text() + '</style>')
    page = page.replace('<script defer src="/app.js"></script>', '')
    page = page.replace('</body>', '<script id="snapshot" type="application/json">' + embedded + '</script><script>' + (root / "app.js").read_text() + '</script></body>')
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination


def export_directory(db, root="."):
    root = Path(root)
    data = db.snapshot()
    (root / "docs").mkdir(parents=True, exist_ok=True)
    for key in ("companies", "people"):
        (root / "docs" / (key + ".json")).write_text(json.dumps(data[key], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {}
    for person in data["people"]:
        counts[person["company_slug"]] = counts.get(person["company_slug"], 0) + 1
    lines = ["# YC directory", "", f"Updated {now()}. {len(data['companies'])} companies; {len(data['people'])} person observations.", "",
             "[Run a year](https://github.com/mallikaakash/yc-people/actions/workflows/collect.yml) · [People CSV](docs/yc-people.csv) · [Companies JSON](docs/companies.json) · [People JSON](docs/people.json)", "",
             "Profile coverage is partial. Company scan status and source notes are available in the HTML directory; GitHub membership does not verify employment.", "",
             "| Company | Batch | People found | Scan status |", "| --- | --- | ---: | --- |"]
    for company in data["companies"]:
        name = company["name"].replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace("\n", " ")
        lines.append(f"| [{name}]({company['yc_url']}) | {company['batch']} | {counts.get(company['slug'], 0)} | {company['scan_status']} |")
    (root / "DIRECTORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


TABLES = ("companies", "people", "profiles", "evidence", "runs")


def save_state(db, path):
    with db.connect() as connection:
        connection.execute("BEGIN")
        result = {"version": 1, "tables": {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in TABLES}}
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def restore_state(db, path):
    source = Path(path)
    if not source.exists():
        return
    result = json.loads(source.read_text())
    if result.get("version") != 1 or set(result.get("tables", {})) != set(TABLES):
        raise ValueError("Unsupported saved-state format")
    with db.connect() as connection:
        if connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0]:
            raise ValueError("Restore requires a fresh database")
        for table in TABLES:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for row in result["tables"][table]:
                if set(row) != set(columns):
                    raise ValueError(f"Saved-state columns do not match {table}")
                connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[key] for key in columns])
