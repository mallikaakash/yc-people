import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .pipeline import run_import, validate_year


def serve(db, port=8765):
    busy = threading.Lock()
    root = Path(__file__).parent / "static"

    class Handler(BaseHTTPRequestHandler):
        def authorized_host(self):
            return self.headers.get("Host") in (f"localhost:{port}", f"127.0.0.1:{port}")

        def send(self, status, body, kind="application/json; charset=utf-8", filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorized_host():
                return self.send(403, {"error": "Local access only"})
            path = urllib.parse.urlsplit(self.path)
            filters = {key: values[0] for key, values in urllib.parse.parse_qs(path.query).items()}
            if path.path == "/api/data":
                return self.send(200, db.snapshot(filters))
            if path.path == "/api/export":
                return self.send(200, db.export_csv(filters).encode("utf-8-sig"), "text/csv; charset=utf-8", "yc-people.csv")
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if path.path in files:
                filename, kind = files[path.path]
                return self.send(200, (root / filename).read_bytes(), kind + "; charset=utf-8")
            self.send(404, {"error": "Not found"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self.authorized_host() or origin not in (None, f"http://localhost:{port}", f"http://127.0.0.1:{port}")
                    or self.headers.get_content_type() != "application/json"):
                return self.send(403, {"error": "Only local JSON requests are accepted"})
            if self.path != "/api/import":
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                year = validate_year(payload.get("year", 0))
                limit = int(payload.get("limit", 0))
                if not 0 <= limit <= 10000:
                    raise ValueError("Invalid scan limit")
                if type(payload.get("refresh", False)) is not bool:
                    raise ValueError("Refresh must be a boolean")
            except (ValueError, TypeError) as error:
                return self.send(400, {"error": str(error)})
            if not busy.acquire(blocking=False):
                return self.send(409, {"error": "An import is already running"})
            run_id = db.start_run(year)

            def run():
                try:
                    run_import(db, year, limit, payload.get("refresh", False), run_id=run_id)
                except Exception as error:
                    print(f"Import {run_id} failed: {error}")
                finally:
                    busy.release()

            threading.Thread(target=run, daemon=True).start()
            self.send(202, {"run_id": run_id})

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"YC People is ready at http://127.0.0.1:{port}", flush=True)
    print(f"Database: {db.path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Saved records remain in SQLite; rerun an import to resume.")
    finally:
        server.server_close()
