import json
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from yc_people.database import Database
from yc_people.pipeline import run_import, validate_year
from yc_people.sources import Page, YCSource, batch_year, canonical_profile, public_address, safe_url, team_people
from yc_people.static_export import export_html, restore_state, save_state


def company(slug="acme", batch="Winter 2024"):
    return {"slug": slug, "name": slug.title(), "batch": batch, "year": 2024, "website": "https://example.com",
            "description": "Example", "team_size": 2, "industry": "B2B", "yc_url": "https://www.ycombinator.com/companies/"+slug}


def person(name="Jane Doe", profile="https://www.linkedin.com/in/jane/?trk=test"):
    return {"name": name, "role": "Founder", "relation": "yc_founder", "profiles": [profile],
            "source_key": "yc:1", "source_url": "https://www.ycombinator.com/companies/acme", "evidence": "YC founder listing"}


class ParsingTests(unittest.TestCase):
    def test_all_four_seasons_and_short_codes(self):
        for season in ("Winter", "Spring", "Summer", "Fall"):
            self.assertEqual(batch_year(season + " 2024"), 2024)
        self.assertEqual(batch_year("X24"), 2024)
        self.assertIsNone(batch_year("Unspecified"))

    def test_dynamic_batch_discovery(self):
        yc = YCSource(None)
        yc.query = lambda _: {"facets": {"batch": {"Spring 2024": 10, "Fall 2024": 20, "Winter 2023": 12, "Summer 2024": 30}}}
        self.assertEqual([b["name"] for b in yc.batches(2024)], ["Spring 2024", "Summer 2024", "Fall 2024"])

    def test_pagination_and_missing_results_are_not_silent(self):
        yc = YCSource(None)
        yc.query = lambda p: {"hits": [company("a" if p["page"] == 0 else "b")], "nbPages": 2, "nbHits": 2}
        self.assertEqual(len(yc.companies("Winter 2024")), 2)
        yc.query = lambda _: {"hits": [company()], "nbPages": 1, "nbHits": 2}
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            yc.companies("Winter 2024")

    def test_profile_normalization(self):
        self.assertEqual(canonical_profile("https://www.linkedin.com/in/Jane/?trk=xyz"), "https://linkedin.com/in/jane")
        self.assertEqual(canonical_profile("https://twitter.com/Jane"), "https://x.com/jane")
        self.assertEqual(canonical_profile("https://linkedin.com/company/acme"), "")
        self.assertEqual(canonical_profile("https://github.com/acme/repo"), "")
        self.assertEqual(canonical_profile("javascript:alert(1)"), "")
        self.assertEqual(safe_url("", "https://company.com"), "")

    def test_yc_underscore_slug_is_preserved(self):
        yc = YCSource(None)
        yc.query = lambda _: {"hits": [company("d_model")], "nbPages": 1, "nbHits": 1}
        self.assertEqual(yc.companies("Summer 2024")[0]["slug"], "d_model")

    def test_structured_people_and_named_links(self):
        content = '''<script type="application/ld+json">{"@graph":[{"@type":"Person","name":"Jane Doe","jobTitle":"Engineer","sameAs":["https://github.com/jane"]}]}</script>
        <a href="https://linkedin.com/in/john">John Smith</a><a href="https://linkedin.com/in/ignored">View profile</a>'''
        people = team_people(Page(content), "https://example.com/team")
        self.assertEqual({p["name"] for p in people}, {"Jane Doe", "John Smith"})
        self.assertEqual(people[0]["profiles"], ["https://github.com/jane"])

    def test_person_without_profiles_not_given_team_page_as_profile(self):
        page = Page('<script type="application/ld+json">{"@type":"Person","name":"Jane"}</script>')
        self.assertEqual(team_people(page, "https://example.com/team"), [])

    def test_internal_addresses_are_rejected(self):
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("127.0.0.1",443))]):
            with self.assertRaises(ValueError):
                public_address("https://example.com")

    def test_invalid_year(self):
        with self.assertRaises(ValueError):
            validate_year(2004)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/"test.sqlite3")
        self.db.upsert_company(company())

    def tearDown(self):
        self.tmp.cleanup()

    def test_reimport_idempotent_and_profile_merges_keep_stronger_evidence(self):
        self.db.save_person("acme", person())
        self.db.save_person("acme", person())
        other = person("jane")
        other.update(source_key="github:jane", relation="github_member", role="Member", source_url="https://github.com/orgs/acme/people")
        self.db.save_person("acme", other)
        rows = self.db.snapshot()["people"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Jane Doe")
        self.assertEqual(rows[0]["relation"], "yc_founder")
        self.assertEqual(len(rows[0]["evidence"]), 2)
        self.assertEqual(len(rows[0]["profiles"]), 1)

    def test_same_name_at_other_company_is_not_merged(self):
        self.db.upsert_company(company("other"))
        self.db.save_person("acme", person())
        self.db.save_person("other", person())
        self.assertEqual(len(self.db.snapshot()["people"]), 2)

    def test_filters_and_csv_formula_safety(self):
        self.db.save_person("acme", person("=DANGEROUS()"))
        self.assertIn("'=DANGEROUS()", self.db.export_csv())
        self.assertEqual(len(self.db.snapshot({"platform":"linkedin"})["people"]), 1)
        self.assertEqual(self.db.snapshot({"q":"missing"})["people"], [])

    def test_partial_results_are_checkpoints_and_failures_retry(self):
        self.db.finish_company("acme", ["GitHub rate limited"])
        self.assertTrue(self.db.completed("acme"))
        self.db.finish_company("acme", ["YC failed"], failed=True)
        self.assertFalse(self.db.completed("acme"))

    def test_state_roundtrip_keeps_ids_profiles_and_evidence(self):
        self.db.save_person("acme", person())
        state = Path(self.tmp.name)/"state.json"
        save_state(self.db, state)
        restored = Database(Path(self.tmp.name)/"restored.sqlite3")
        restore_state(restored, state)
        self.assertEqual(restored.snapshot()["people"], self.db.snapshot()["people"])
        with self.assertRaises(ValueError):
            restore_state(restored, state)

    def test_html_is_self_contained_and_escapes_script_injection(self):
        self.db.save_person("acme", person('</script><script>alert(1)</script>'))
        path = export_html(self.db, Path(self.tmp.name)/"index.html")
        html = path.read_text()
        self.assertIn('id="snapshot"', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertNotIn('src="/app.js"', html)
        self.assertNotIn('href="/style.css"', html)
        self.assertNotIn(self.db.path, html)

    def test_stale_scan_becomes_eligible_for_refresh(self):
        self.db.finish_company("acme", [])
        self.assertTrue(self.db.completed("acme", refresh_after_days=30))
        with self.db.connect() as connection:
            connection.execute("UPDATE companies SET scanned_at='2020-01-01T00:00:00+00:00'")
        self.assertFalse(self.db.completed("acme", refresh_after_days=30))

    def test_import_resumes_and_discovery_ignores_scan_limit(self):
        class FakeYC:
            def __init__(self, fetch): pass
            def batches(self, year): return [{"name":"Winter 2024","count":2},{"name":"Fall 2024","count":1}]
            def companies(self, batch): return [company("a",batch), company("b",batch)] if batch.startswith("Winter") else [company("c",batch)]
            def details(self, c): return [person()], "", ""
        with patch("yc_people.pipeline.YCSource", FakeYC), patch("yc_people.pipeline.website_people", return_value=([],[],[])):
            run_import(self.db, 2024, limit=1, log=lambda _:None)
            self.assertEqual(len(self.db.snapshot()["companies"]), 4)  # Existing acme plus all three discovered.
            self.assertEqual(len(self.db.snapshot()["people"]), 1)
            run_import(self.db, 2024, log=lambda _:None)
            self.assertEqual(len(self.db.snapshot()["people"]), 3)
            run_import(self.db, 2024, log=lambda _:None)
            self.assertEqual(self.db.snapshot()["runs"][0]["total"], 0)

    def test_source_failure_does_not_erase_founders(self):
        class FakeYC:
            def __init__(self, fetch): pass
            def batches(self, year): return [{"name":"Winter 2024","count":1}]
            def companies(self, batch): return [company()]
            def details(self, c): return [person()], "https://example.com", ""
        with patch("yc_people.pipeline.YCSource", FakeYC), patch("yc_people.pipeline.website_people", side_effect=RuntimeError("blocked")):
            run_import(self.db, 2024, log=lambda _:None)
        result=self.db.snapshot()
        self.assertEqual(len(result["people"]), 1)
        self.assertEqual(result["companies"][0]["scan_status"], "partial")
        self.assertEqual(result["runs"][0]["status"], "completed_with_notes")

    def test_workers_really_overlap_and_checkpoint_restores_consistently(self):
        barrier = threading.Barrier(2)
        class FakeYC:
            def __init__(self, fetch): pass
            def batches(self, year): return [{"name":"Winter 2024","count":2}]
            def companies(self, batch): return [company("first"), company("second")]
            def details(self, c):
                barrier.wait(timeout=3)
                return [person()], "", ""
        checkpoint_path = Path(self.tmp.name)/"checkpoint.json"
        with patch("yc_people.pipeline.YCSource", FakeYC), patch("yc_people.pipeline.website_people", return_value=([],[],[])):
            run_import(self.db, 2024, workers=2, log=lambda _:None,
                       checkpoint=lambda: save_state(self.db, checkpoint_path))
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["runs"][0]["processed"], 2)
        self.assertEqual(snapshot["runs"][0]["failed"], 0)
        self.assertEqual(len(snapshot["people"]), 2)
        self.assertTrue(checkpoint_path.exists())
        save_state(self.db, checkpoint_path)
        restored = Database(Path(self.tmp.name)/"parallel.sqlite3")
        restore_state(restored, checkpoint_path)
        self.assertEqual(restored.snapshot()["people"], snapshot["people"])

    def test_time_budget_is_reported_as_incomplete(self):
        class FakeYC:
            def __init__(self, fetch): pass
            def batches(self, year): return [{"name":"Winter 2024","count":1}]
            def companies(self, batch): return [company()]
        with patch("yc_people.pipeline.YCSource", FakeYC), patch("yc_people.pipeline.time.monotonic", side_effect=[0,2]):
            run_import(self.db, 2024, max_seconds=1, log=lambda _:None)
        run = self.db.snapshot()["runs"][0]
        self.assertEqual(run["status"], "incomplete")
        self.assertEqual(run["processed"], 0)


if __name__ == "__main__":
    unittest.main()
