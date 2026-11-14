"""Small HTTP server: a JSON API router plus static file delivery for the frontend."""

import email.utils
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple, Type
from urllib.parse import parse_qs, unquote, urlsplit

log = logging.getLogger(__name__)

MAX_BODY = 1 << 20  # API bodies are tiny; refuse anything larger

# Explicit types: mimetypes reads /etc/mime.types, which differs between the Mac and Debian.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


def worth_logging(code: Any) -> bool:
    """Access log: only failed requests. The web UI polls about once a second, so logging
    every 200/304 would bury everything else."""
    try:
        return int(code) >= 400
    except (TypeError, ValueError):
        return True  # "-" etc.: something unusual, keep it


class QuietHTTPServer(ThreadingHTTPServer):
    """Clients on the LAN (TVs, phones) drop connections all the time; log that, no traceback."""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            log.debug("connection from %s dropped: %s", client_address[0], exc)
        else:
            super().handle_error(request, client_address)


class HTTPError(Exception):
    """Raised by a route: becomes a JSON error response `{"ok": false, "error": ...}`."""

    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.extra = status, extra


@dataclass
class Request:
    method: str
    path: str
    params: Dict[str, str]  # from {placeholders} in the route
    query: Dict[str, List[str]]
    headers: Any
    body: bytes = b""
    client: str = ""
    _json: Any = field(default=None, repr=False)

    def json(self) -> Dict[str, Any]:
        """The body as a JSON object; an empty body is `{}`."""
        if self._json is None:
            try:
                data = json.loads(self.body or b"{}")
            except ValueError:
                raise HTTPError(400, "body is not valid JSON") from None
            if not isinstance(data, dict):
                raise HTTPError(400, "expected a JSON object")
            self._json = data
        return self._json


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: Dict[str, str] = field(default_factory=dict)


def json_response(data: Any, status: int = 200) -> Response:
    return Response(status, json.dumps(data).encode(), "application/json")


Handler = Callable[[Request], Any]  # returns a Response, or data to send as JSON


class Router:
    """Routes like `PUT /api/outputs/{id}`; `{name}` matches one path segment."""

    def __init__(self) -> None:
        self.routes: List[Tuple[str, Pattern[str], str, Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        def segment(part: str) -> str:
            if part.startswith("{") and part.endswith("}"):
                return f"(?P<{part[1:-1]}>[^/]+)"
            return re.escape(part)
        regex = "/".join(segment(part) for part in pattern.split("/"))
        self.routes.append((method.upper(), re.compile(f"^{regex}$"), pattern, handler))

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self.add(method, pattern, handler)
            return handler
        return decorator

    def match(self, method: str, path: str) -> Tuple[Optional[Handler], Dict[str, str], bool]:
        """(handler, params, path_known). path_known without handler means 405."""
        known = False
        for m, regex, _pattern, handler in self.routes:
            found = regex.match(path)
            if found:
                known = True
                if m == method:
                    return handler, {k: unquote(v) for k, v in found.groupdict().items()}, True
        return None, {}, known

    def allowed(self, path: str) -> List[str]:
        return sorted({m for m, regex, _p, _h in self.routes if regex.match(path)})


class StaticFiles:
    """Serves files below `root`; `/` and directories map to index.html.

    Sent with an ETag and `Cache-Control: no-cache`: the browser revalidates every time, so an
    update from the web UI shows the new frontend on the next reload (304 when unchanged)."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, url_path: str) -> Optional[Path]:
        rel = unquote(url_path).lstrip("/")
        if "\0" in rel:
            return None
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents:
            return None  # ../ outside the frontend directory
        if path.is_dir():
            path = path / "index.html"
        return path if path.is_file() else None

    def response(self, url_path: str, headers: Any) -> Optional[Response]:
        path = self.resolve(url_path)
        if path is None:
            return None
        data = path.read_bytes()
        etag = '"' + hashlib.sha1(data).hexdigest()[:16] + '"'
        common = {"ETag": etag, "Cache-Control": "no-cache",
                  "Last-Modified": email.utils.formatdate(path.stat().st_mtime, usegmt=True)}
        if etag in (headers.get("If-None-Match") or ""):
            return Response(304, b"", "", common)
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return Response(200, data, ctype, common)


class HTTPApp:
    """Glue for BaseHTTPRequestHandler: API routes under the router, everything else static."""

    def __init__(self, router: Router, static: Optional[StaticFiles] = None,
                 errors: Optional[Dict[Type[BaseException], Callable[[BaseException],
                                                                     Response]]] = None,
                 server_name: str = "wormhole"):
        self.router, self.static, self.server_name = router, static, server_name
        self.errors = errors or {}  # exception type -> response (e.g. ValueError -> 400)

    def dispatch(self, req: Request) -> Response:
        handler, params, known = self.router.match(req.method, req.path)
        if handler is None:
            if known:
                resp = json_response({"ok": False, "error": "method not allowed"}, 405)
                resp.headers["Allow"] = ", ".join(self.router.allowed(req.path))
                return resp
            if req.method in ("GET", "HEAD") and self.static is not None \
                    and not req.path.startswith("/api/"):
                resp = self.static.response(req.path, req.headers)
                if resp is not None:
                    return resp
            return json_response({"ok": False, "error": f"not found: {req.path}"}, 404)
        req.params = params
        try:
            result = handler(req)
        except HTTPError as exc:
            return json_response(dict(exc.extra, ok=False, error=str(exc)), exc.status)
        except Exception as exc:  # noqa: BLE001 - mapped below or reported as 500
            for kind, make in self.errors.items():
                if isinstance(exc, kind):
                    return make(exc)
            log.exception("%s %s failed", req.method, req.path)
            return json_response({"ok": False, "error": f"internal error: {exc}"}, 500)
        if isinstance(result, Response):
            return result
        return json_response({"ok": True} if result is None else result)

    def handler_class(self) -> type:
        app = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = app.server_name

            def log_message(self, fmt: str, *args) -> None:
                log.debug("%s %s", self.address_string(), fmt % args)

            def log_request(self, code: Any = "-", size: Any = "-") -> None:
                if worth_logging(code):
                    super().log_request(code, size)

            def do_GET(self) -> None:
                self._run()

            def do_HEAD(self) -> None:
                self._run()

            def do_POST(self) -> None:
                self._run()

            def do_PUT(self) -> None:
                self._run()

            def do_DELETE(self) -> None:
                self._run()

            def _run(self) -> None:
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(json_response({"ok": False, "error": "body too large"},
                                                    413))
                body = self.rfile.read(length) if length else b""
                method = "GET" if self.command == "HEAD" else self.command
                req = Request(method, parts.path, {}, parse_qs(parts.query), self.headers,
                              body, self.client_address[0])
                self._send(app.dispatch(req))

            def _send(self, resp: Response) -> None:
                self.send_response(resp.status)
                if resp.content_type:
                    self.send_header("Content-Type", resp.content_type)
                headers = dict(resp.headers)
                headers.setdefault("Cache-Control", "no-store")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(resp.body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(resp.body)

        return RequestHandler
