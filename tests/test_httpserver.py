"""Router, static files and error handling of httpserver (loopback only)."""

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from wormhole.httpserver import (HTTPApp, HTTPError, QuietHTTPServer, Response, Router,
                                 StaticFiles, worth_logging)


class LogFilterTest(unittest.TestCase):
    def test_worth_logging(self):
        from http import HTTPStatus
        self.assertFalse(worth_logging(200))
        self.assertFalse(worth_logging(HTTPStatus.NOT_MODIFIED))
        self.assertTrue(worth_logging(404))
        self.assertTrue(worth_logging(HTTPStatus.INTERNAL_SERVER_ERROR))
        self.assertTrue(worth_logging("-"))


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.r = Router()
        self.r.add("GET", "/api/items", lambda req: "list")
        self.r.add("PUT", "/api/items/{id}", lambda req: "put")
        self.r.add("POST", "/api/items/{id}/pair", lambda req: "pair")

    def test_placeholders(self):
        handler, params, _ = self.r.match("PUT", "/api/items/a%20b")
        self.assertEqual((handler(None), params), ("put", {"id": "a b"}))
        handler, params, _ = self.r.match("POST", "/api/items/7/pair")
        self.assertEqual((handler(None), params), ("pair", {"id": "7"}))

    def test_placeholder_is_one_segment(self):
        self.assertEqual(self.r.match("PUT", "/api/items/7/x"), (None, {}, False))

    def test_wrong_method_is_known_path(self):
        self.assertEqual(self.r.match("DELETE", "/api/items/7"), (None, {}, True))
        self.assertEqual(self.r.allowed("/api/items/7"), ["PUT"])

    def test_literal_parts_are_not_regex(self):
        r = Router()
        r.add("GET", "/a.b", lambda req: None)
        self.assertIsNone(r.match("GET", "/axb")[0])


class ServerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "index.html").write_text("<title>t</title>")
        (root / "js").mkdir()
        (root / "js" / "app.js").write_text("export {};")
        (root.parent / f"{root.name}-secret.txt").write_text("secret")
        self.addCleanup((root.parent / f"{root.name}-secret.txt").unlink)
        self.root = root

        router = Router()
        router.add("GET", "/api/data", lambda req: {"n": 1})
        router.add("POST", "/api/echo/{name}",
                   lambda req: {"name": req.params["name"], "body": req.json()})
        router.add("POST", "/api/none", lambda req: None)
        router.add("GET", "/api/teapot", lambda req: Response(418, b"tea"))

        def fail(req):
            raise HTTPError(409, "busy", retry=True)
        router.add("POST", "/api/fail", fail)

        def value_error(req):
            raise ValueError("bad value")
        router.add("POST", "/api/value", value_error)

        def crash(req):
            raise RuntimeError("boom")
        router.add("POST", "/api/crash", crash)

        errors = {ValueError: lambda exc: Response(400, str(exc).encode())}
        app = HTTPApp(router, StaticFiles(root), errors)
        self.server = QuietHTTPServer(("127.0.0.1", 0), app.handler_class())
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def fetch(self, path, method="GET", data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, dict(exc.headers), exc.read()

    def test_json_routes(self):
        self.assertEqual(self.fetch("/api/data")[2], b'{"n": 1}')
        status, _, body = self.fetch("/api/echo/x", "POST", b'{"a": 2}')
        self.assertEqual((status, body), (200, b'{"name": "x", "body": {"a": 2}}'))
        self.assertEqual(self.fetch("/api/none", "POST")[2], b'{"ok": true}')
        self.assertEqual(self.fetch("/api/teapot")[:1], (418,))

    def test_errors(self):
        self.assertEqual(self.fetch("/api/fail", "POST")[::2],
                         (409, b'{"retry": true, "ok": false, "error": "busy"}'))
        self.assertEqual(self.fetch("/api/value", "POST")[::2], (400, b"bad value"))
        with self.assertLogs("wormhole.httpserver", "ERROR"):
            status, _, body = self.fetch("/api/crash", "POST")
        self.assertEqual(status, 500)
        self.assertIn(b"boom", body)

    def test_bad_json_body(self):
        status, _, body = self.fetch("/api/echo/x", "POST", b"[1]")
        self.assertEqual(status, 400)
        self.assertIn(b"JSON object", body)
        self.assertEqual(self.fetch("/api/echo/x", "POST", b"{nope")[0], 400)

    def test_not_found_and_method_not_allowed(self):
        self.assertEqual(self.fetch("/api/missing")[0], 404)
        status, headers, _ = self.fetch("/api/data", "POST")
        self.assertEqual((status, headers["Allow"]), (405, "GET"))
        self.assertEqual(self.fetch("/missing.js")[0], 404)

    def test_static_files_with_types(self):
        status, headers, body = self.fetch("/")
        self.assertEqual((status, body), (200, b"<title>t</title>"))
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        status, headers, _ = self.fetch("/js/app.js")
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_etag_gives_304(self):
        etag = self.fetch("/js/app.js")[1]["ETag"]
        status, _, body = self.fetch("/js/app.js", headers={"If-None-Match": etag})
        self.assertEqual((status, body), (304, b""))
        (self.root / "js" / "app.js").write_text("export const changed = 1;")
        self.assertEqual(self.fetch("/js/app.js", headers={"If-None-Match": etag})[0], 200)

    def test_head_has_no_body(self):
        status, headers, body = self.fetch("/", "HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(headers["Content-Length"], str(len("<title>t</title>")))

    def test_access_log_only_has_failed_requests(self):
        with self.assertLogs("wormhole.httpserver", "DEBUG") as logs:
            self.fetch("/api/data")
            etag = self.fetch("/")[1]["ETag"]
            self.fetch("/", headers={"If-None-Match": etag})  # 304
            self.fetch("/api/missing")
        lines = [r.getMessage() for r in logs.records]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('"GET /api/missing HTTP/1.1" 404', lines[0])

    def test_no_files_outside_the_root(self):
        static = StaticFiles(self.root)
        for path in (f"/../{self.root.name}-secret.txt", "/%2e%2e/etc/passwd", "/js/../../x",
                     "/a\0b"):
            self.assertIsNone(static.resolve(path), path)
        self.assertEqual(self.fetch(f"/%2e%2e/{self.root.name}-secret.txt")[0], 404)


if __name__ == "__main__":
    unittest.main()
