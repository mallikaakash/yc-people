import csv
import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .sources import canonical_profile, platform


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path="data/yc_people.sqlite3"):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS companies (
                    slug TEXT PRIMARY KEY, name TEXT NOT NULL, batch TEXT NOT NULL, year INTEGER NOT NULL,
                    website TEXT, description TEXT, team_size INTEGER, industry TEXT, yc_url TEXT,
                    imported_at TEXT NOT NULL, scanned_at TEXT, scan_status TEXT DEFAULT 'pending',
                    warnings TEXT DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY, company_slug TEXT NOT NULL REFERENCES companies(slug),
                    source_key TEXT NOT NULL, name TEXT NOT NULL, role TEXT, relation TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(company_slug, source_key)
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    person_id INTEGER NOT NULL REFERENCES people(id), url TEXT NOT NULL,
                    platform TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    PRIMARY KEY(person_id, url)
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    person_id INTEGER NOT NULL REFERENCES people(id), source_url TEXT NOT NULL,
                    relation TEXT NOT NULL, note TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    PRIMARY KEY(person_id, source_url, relation)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, year INTEGER NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, total INTEGER DEFAULT 0,
                    processed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, message TEXT, batches TEXT DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS companies_year ON companies(year);
                CREATE INDEX IF NOT EXISTS people_company ON people(company_slug);
                CREATE INDEX IF NOT EXISTS profiles_url ON profiles(url);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def upsert_company(self, company):
        fields = ("slug", "name", "batch", "year", "website", "description", "team_size", "industry", "yc_url")
        with self.connect() as db:
            db.execute(f"""INSERT INTO companies ({','.join(fields)}, imported_at)
                VALUES ({','.join('?' for _ in range(len(fields)+1))})
                ON CONFLICT(slug) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in fields if k != 'slug')},
                imported_at=excluded.imported_at""", [company.get(k) for k in fields] + [now()])

    def save_person(self, slug, person):
        timestamp = now()
        profiles = sorted({p for url in person["profiles"] if (p := canonical_profile(url))})
        with self.connect() as db:
            existing = db.execute("SELECT id, relation FROM people WHERE company_slug=? AND source_key=?",
                                  (slug, person["source_key"])).fetchone()
            # Match exact public profiles within the same company, never a name alone across companies.
            if not existing and profiles:
                matches = db.execute(f"""SELECT DISTINCT p.id,p.relation FROM people p JOIN profiles u ON u.person_id=p.id
                    WHERE p.company_slug=? AND u.url IN ({','.join('?' for _ in profiles)})""", [slug, *profiles]).fetchall()
                if len(matches) == 1:
                    existing = matches[0]
            if existing:
                person_id = existing["id"]
                strength = {"yc_founder": 3, "company_listed": 2, "github_member": 1}
                if strength[person["relation"]] >= strength[existing["relation"]]:
                    db.execute("UPDATE people SET name=?,role=?,relation=?,last_seen=? WHERE id=?",
                               (person["name"], person["role"], person["relation"], timestamp, person_id))
                else:
                    db.execute("UPDATE people SET last_seen=? WHERE id=?", (timestamp, person_id))
            else:
                person_id = db.execute("""INSERT INTO people
                    (company_slug,source_key,name,role,relation,first_seen,last_seen) VALUES (?,?,?,?,?,?,?)""",
                    (slug, person["source_key"], person["name"], person["role"], person["relation"], timestamp, timestamp)).lastrowid
            for url in profiles:
                db.execute("""INSERT INTO profiles VALUES (?,?,?,?,?) ON CONFLICT(person_id,url)
                    DO UPDATE SET last_seen=excluded.last_seen""", (person_id, url, platform(url), timestamp, timestamp))
            db.execute("""INSERT INTO evidence VALUES (?,?,?,?,?,?) ON CONFLICT(person_id,source_url,relation)
                DO UPDATE SET note=excluded.note,last_seen=excluded.last_seen""",
                (person_id, person["source_url"], person["relation"], person["evidence"], timestamp, timestamp))

    def finish_company(self, slug, warnings, failed=False):
        with self.connect() as db:
            db.execute("UPDATE companies SET scanned_at=?,scan_status=?,warnings=? WHERE slug=?",
                       (now(), "failed" if failed else "partial" if warnings else "complete", json.dumps(warnings), slug))

    def completed(self, slug, refresh_after_days=0):
        with self.connect() as db:
            row = db.execute("SELECT scan_status,scanned_at FROM companies WHERE slug=?", (slug,)).fetchone()
            complete = bool(row and row[0] in ("complete", "partial"))
            if complete and refresh_after_days:
                return datetime.fromisoformat(row[1]) > datetime.now(timezone.utc) - timedelta(days=refresh_after_days)
            return complete

    def start_run(self, year):
        with self.connect() as db:
            return db.execute("INSERT INTO runs(year,status,started_at,message) VALUES (?,?,?,?)",
                              (year, "queued", now(), "Discovering YC batches…")).lastrowid

    def update_run(self, run_id, **fields):
        allowed = {"status", "finished_at", "total", "processed", "failed", "message", "batches"}
        assert set(fields) <= allowed
        with self.connect() as db:
            db.execute(f"UPDATE runs SET {','.join(k+'=?' for k in fields)} WHERE id=?", [*fields.values(), run_id])

    def interrupt_runs(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='Process stopped; run the same year to resume.' WHERE status='running'", (now(),))

    def snapshot(self, filters=None):
        filters = filters or {}
        clauses, params = [], []
        for field in ("year", "batch"):
            if filters.get(field):
                clauses.append(f"c.{field}=?")
                params.append(filters[field])
        if filters.get("q"):
            clauses.append("(c.name LIKE ? OR p.name LIKE ? OR p.role LIKE ?)")
            params.extend(["%" + filters["q"] + "%"] * 3)
        if filters.get("relation"):
            clauses.append("p.relation=?")
            params.append(filters["relation"])
        if filters.get("platform"):
            clauses.append("EXISTS (SELECT 1 FROM profiles u WHERE u.person_id=p.id AND u.platform=?)")
            params.append(filters["platform"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            people = [dict(row) for row in db.execute("""SELECT p.*, c.name AS company,c.batch,c.year,c.yc_url
                FROM people p JOIN companies c ON c.slug=p.company_slug""" + where + " ORDER BY c.name,p.name", params)]
            by_id = {p["id"]: p for p in people}
            for p in people:
                p["profiles"], p["evidence"] = [], []
            for row in db.execute("SELECT * FROM profiles"):
                if row["person_id"] in by_id:
                    by_id[row["person_id"]]["profiles"].append(dict(row))
            for row in db.execute("SELECT * FROM evidence"):
                if row["person_id"] in by_id:
                    by_id[row["person_id"]]["evidence"].append(dict(row))
            company_where, company_params = [], []
            for key in ("year", "batch"):
                if filters.get(key):
                    company_where.append(key + "=?")
                    company_params.append(filters[key])
            company_sql = " WHERE " + " AND ".join(company_where) if company_where else ""
            companies = [dict(row) for row in db.execute("SELECT * FROM companies" + company_sql + " ORDER BY name", company_params)]
            for company in companies:
                company["warnings"] = json.loads(company["warnings"])
            runs = [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10")]
            years = [row[0] for row in db.execute("SELECT DISTINCT year FROM companies ORDER BY year DESC")]
            count = db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        return {"companies": companies, "people": people, "runs": runs, "years": years,
                "profile_count": count, "database": self.path}

    def export_csv(self, filters=None):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["company", "batch", "person", "role", "affiliation", "platform", "profile_url", "source_urls", "first_seen", "last_seen"])
        for person in self.snapshot(filters)["people"]:
            for profile in person["profiles"] or [{"platform": "", "url": ""}]:
                row = [person["company"], person["batch"], person["name"], person["role"], person["relation"],
                       profile["platform"], profile["url"], " | ".join(e["source_url"] for e in person["evidence"]),
                       person["first_seen"], person["last_seen"]]
                writer.writerow(["'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for v in row])
        return stream.getvalue()
