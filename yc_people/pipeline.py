import json
import fcntl
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime

from .database import now
from .sources import Fetcher, YCSource, github_members, website_people


def validate_year(value):
    year = int(value)
    if not 2005 <= year <= datetime.now().year:
        raise ValueError("Choose a year between 2005 and the current year")
    return year


def run_import(db, year, limit=0, refresh=False, enrich=True, run_id=None, log=print, refresh_after_days=0, max_seconds=0, workers=8, checkpoint=None):
    year = validate_year(year)
    if not 1 <= workers <= 12:
        raise ValueError("Workers must be between 1 and 12")
    with open(db.path + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            if run_id:
                db.update_run(run_id, status="failed", finished_at=now(), message="Another process is importing into this database")
            raise RuntimeError("Another process is importing into this database") from error
        db.interrupt_runs()
        return _run_import(db, year, limit, refresh, enrich, run_id, log, refresh_after_days, max_seconds, workers, checkpoint)


def _run_import(db, year, limit=0, refresh=False, enrich=True, run_id=None, log=print, refresh_after_days=0, max_seconds=0, workers=8, checkpoint=None):
    started = time.monotonic()
    year = validate_year(year)
    if not 0 <= limit <= 10000:
        raise ValueError("Limit must be between 0 and 10000 (0 means every company)")
    run_id = run_id or db.start_run(year)
    db.update_run(run_id, status="running")
    processed, failed, partial = 0, 0, 0
    try:
        fetch = Fetcher()
        yc = YCSource(fetch)
        batches = yc.batches(year)
        if not batches:
            raise RuntimeError(f"YC currently lists no public batches for {year}")
        db.update_run(run_id, batches=json.dumps(batches))
        companies = {}
        for batch in batches:
            db.update_run(run_id, message=f"Discovering {batch['name']} ({batch['count']} listed companies)…")
            for company in yc.companies(batch["name"]):
                db.upsert_company(company)
                companies[company["slug"]] = company
        selected = sorted(companies.values(), key=lambda c: (c["batch"], c["name"].casefold()))
        if not refresh:
            selected = [c for c in selected if not db.completed(c["slug"], refresh_after_days)]
        if refresh_after_days:
            with db.connect() as connection:
                dates = {row["slug"]: row["scanned_at"] or "" for row in connection.execute("SELECT slug,scanned_at FROM companies")}
            selected.sort(key=lambda c: dates.get(c["slug"], ""))
        if limit:
            selected = selected[:limit]
        db.update_run(run_id, total=len(selected))
        if checkpoint:
            checkpoint()

        def collect_company(company):
            warnings, fatal = [], False
            try:
                people, website, github = yc.details(company)
                for person in people:
                    db.save_person(company["slug"], person)
                orgs = [github.rsplit("/", 1)[-1]] if github.startswith("https://github.com/") else []
                if enrich:
                    try:
                        additional, linked_orgs, notes = website_people(fetch, website or company["website"])
                        warnings.extend(notes)
                        orgs.extend(linked_orgs)
                        for person in additional:
                            db.save_person(company["slug"], person)
                    except Exception as error:
                        warnings.append(f"Company website: {error}")
                    for org in sorted(set(orgs))[:2]:
                        try:
                            members, notes = github_members(fetch, org)
                            warnings.extend(notes)
                            for person in members:
                                db.save_person(company["slug"], person)
                        except Exception as error:
                            warnings.append(f"GitHub {org}: {error}")
                else:
                    warnings.append("Website and GitHub discovery disabled for this scan")
            except Exception as error:
                warnings.append(str(error))
                fatal = True
            db.finish_company(company["slug"], warnings, failed=fatal)
            return company, warnings, fatal

        db.update_run(run_id, message=f"Collecting {len(selected)} companies with {workers} parallel workers")
        # Keep only workers tasks in flight, so a time budget does not queue an entire year.
        iterator = iter(selected)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = set()

            def submit_one():
                if max_seconds and time.monotonic() - started >= max_seconds:
                    return False
                company = next(iterator, None)
                if company is None:
                    return False
                pending.add(pool.submit(collect_company, company))
                return True

            for _ in range(workers):
                if not submit_one():
                    break
            while pending:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    company, warnings, fatal = future.result()
                    failed += int(fatal)
                    partial += int(bool(warnings) and not fatal)
                    processed += 1
                    db.update_run(run_id, processed=processed, failed=failed,
                                  message=f"{processed}/{len(selected)} scanned · Last finished: {company['name']}")
                    log(f"[{processed}/{len(selected)}] {company['name']}: " + ("; ".join(warnings) if warnings else "collected"))
                    if checkpoint and processed % 10 == 0:
                        checkpoint()
                    submit_one()
        message = f"Discovered {len(companies)} companies across {len(batches)} batches. Scanned {processed}; {partial} with source notes; failed {failed}."
        if limit:
            message += f" Profile scan limit: {limit}. Run again to continue."
        if processed < len(selected):
            message += " Time budget reached; remaining companies will resume on the next run."
        status = "incomplete" if processed < len(selected) else "completed_with_errors" if failed else "completed_with_notes" if partial else "completed"
        db.update_run(run_id, status=status, finished_at=now(), message=message)
        log(message)
    except Exception as error:
        db.update_run(run_id, status="failed", finished_at=now(), message=str(error), processed=processed, failed=failed)
        raise
    return run_id
