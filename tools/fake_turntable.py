#!/usr/bin/env python3
"""Fake turntable: stand-in for the Pro-Ject T2 W during development (STRATEGY.md §2).

Streams an audio file (or a 440 Hz test tone) as a live, non-seekable MP3 or FLAC stream via
ffmpeg and tells a UPnP renderer to play it (AVTransport SetAVTransportURI + Play). Includes a
small web UI to pick the speaker and press play / pause / stop.

Standard library only, Python 3.9+. Needs ffmpeg on PATH.

  python3 tools/fake_turntable.py                         # web UI on http://localhost:8765
  python3 tools/fake_turntable.py --list                  # discover renderers
  python3 tools/fake_turntable.py --renderer NAME [--source FILE|tone] [--format mp3|flac]
  python3 tools/fake_turntable.py --renderer NAME --stop

Audio files: put them in tools/media/ (or pass a path with --source).
"""

import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from xml.sax.saxutils import escape

log = logging.getLogger("fake_turntable")

DEFAULT_PORT = int(os.environ.get("STREAM_PORT", "8765"))
DEFAULT_MEDIA_DIR = Path(__file__).resolve().parent / "media"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus"}
TONE = "tone"

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
)

# MIME type sent as Content-Type and in the DIDL-Lite protocolInfo.
FORMATS = {"mp3": "audio/mpeg", "flac": "audio/flac"}

# Fallback text for common UPnP errors when the renderer sends a code without description
# (UPnP Device Architecture + AVTransport:1 service spec).
UPNP_ERRORS = {
    401: "Invalid Action",
    402: "Invalid Args",
    501: "Action Failed",
    701: "Transition not available",
    714: "Illegal MIME-type",
    716: "Resource not found",
    718: "Invalid InstanceID",
}


# --------------------------------------------------------------------------- UPnP discovery

@dataclass
class Renderer:
    key: str                        # UDN, or location if the device has none
    name: str
    manufacturer: str
    model: str
    host: str
    location: str
    avtransport_url: Optional[str]  # control URL; None = not a media renderer
    avtransport_type: str

    @property
    def can_play(self) -> bool:
        return self.avtransport_url is not None


def fix_host(url: str, host: str) -> str:
    """Replace an unusable host (0.0.0.0 / missing) with the address the reply came from.

    Seen in the wild: a Hue bridge advertises LOCATION: http://0.0.0.0:80/description.xml.
    """
    parts = urlsplit(url)
    if parts.hostname in (None, "", "0.0.0.0"):
        netloc = host if parts.port is None else f"{host}:{parts.port}"
        return urlunsplit((parts.scheme or "http", netloc, parts.path, parts.query, ""))
    return url


def parse_ssdp_response(data: bytes) -> Dict[str, str]:
    headers = {}
    for line in data.decode("utf-8", "replace").split("\r\n")[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return headers


def ssdp_search(timeout: float) -> Dict[str, str]:
    """Multicast M-SEARCH. Returns {description URL: responder IP}."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.3)
    mx = max(1, int(timeout) - 1)
    try:
        for _ in range(2):  # UDP may drop a packet; send each search twice
            for st in SEARCH_TARGETS:
                msg = (
                    "M-SEARCH * HTTP/1.1\r\n"
                    f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                    'MAN: "ssdp:discover"\r\n'
                    f"MX: {mx}\r\nST: {st}\r\n\r\n"
                )
                sock.sendto(msg.encode(), SSDP_ADDR)
        found: Dict[str, str] = {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            location = parse_ssdp_response(data).get("location")
            if location:
                found[fix_host(location, addr[0])] = addr[0]
        return found
    finally:
        sock.close()


def _text(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_description(xml: bytes, location: str, host: str) -> Optional[Renderer]:
    root = ET.fromstring(xml)
    device = root.find("{*}device")
    if device is None:
        return None
    base = fix_host(_text(root.find("{*}URLBase")) or location, host)
    control, service_type = None, ""
    # iterfind, not iter: only ElementPath understands the {*} namespace wildcard.
    for svc in device.iterfind(".//{*}service"):  # includes embedded devices
        stype = _text(svc.find("{*}serviceType"))
        if ":service:AVTransport:" in stype:
            control = urljoin(base, _text(svc.find("{*}controlURL")))
            service_type = stype
            break
    return Renderer(
        key=_text(device.find("{*}UDN")) or location,
        name=_text(device.find("{*}friendlyName")) or host,
        manufacturer=_text(device.find("{*}manufacturer")),
        model=_text(device.find("{*}modelName")),
        host=host,
        location=location,
        avtransport_url=control,
        avtransport_type=service_type,
    )


def describe(location: str, host: str) -> Optional[Renderer]:
    try:
        with urllib.request.urlopen(location, timeout=3) as resp:
            return parse_description(resp.read(), location, host)
    except (OSError, ET.ParseError) as exc:
        log.debug("cannot read %s: %s", location, exc)
        return None


def discover(timeout: float = 3.0) -> List[Renderer]:
    locations = ssdp_search(timeout)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda item: describe(*item), locations.items())
    unique = {r.key: r for r in results if r is not None}
    return sorted(unique.values(), key=lambda r: (not r.can_play, r.name.lower()))


# --------------------------------------------------------------------------- UPnP control

class UPnPError(Exception):
    def __init__(self, action: str, code: Optional[int], description: str):
        self.action, self.code, self.description = action, code, description
        label = f"UPnP error {code}" if code is not None else "error"
        super().__init__(f"{action} failed: {label}: {description}")


def soap_envelope(service_type: str, action: str, args: List[Tuple[str, str]]) -> bytes:
    inner = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in args)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service_type}">{inner}</u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode()


def parse_fault(body: bytes) -> Tuple[Optional[int], str]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None, body[:200].decode("utf-8", "replace")
    code_text = _text(root.find(".//{*}errorCode"))
    code = int(code_text) if code_text.isdigit() else None
    desc = _text(root.find(".//{*}errorDescription"))
    if not desc and code in UPNP_ERRORS:
        desc = f"{UPNP_ERRORS[code]} (spec meaning; renderer sent no description)"
    if not desc:
        desc = _text(root.find(".//faultstring"))  # usually just "UPnPError"
    return code, desc or "no description"


def soap_call(r: Renderer, action: str, args: List[Tuple[str, str]],
              timeout: float = 5.0) -> Dict[str, str]:
    if not r.avtransport_url:
        raise UPnPError(action, None, f"{r.name} has no AVTransport service")
    req = urllib.request.Request(
        r.avtransport_url,
        data=soap_envelope(r.avtransport_type, action, args),
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{r.avtransport_type}#{action}"',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        with exc:
            code, desc = parse_fault(exc.read())
        raise UPnPError(action, code, f"{desc} (HTTP {exc.code})") from None
    except OSError as exc:
        raise UPnPError(action, None, f"cannot reach {r.avtransport_url}: {exc}") from None
    out: Dict[str, str] = {}
    for el in ET.fromstring(body).iter():
        if el.tag.endswith(f"{action}Response"):
            out = {child.tag.split("}")[-1]: (child.text or "") for child in el}
    return out


def didl_lite(title: str, url: str, mime: str) -> str:
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


def set_uri(r: Renderer, url: str, metadata: str) -> None:
    soap_call(r, "SetAVTransportURI",
              [("InstanceID", "0"), ("CurrentURI", url), ("CurrentURIMetaData", metadata)])


def play(r: Renderer) -> None:
    soap_call(r, "Play", [("InstanceID", "0"), ("Speed", "1")])


def pause(r: Renderer) -> None:
    soap_call(r, "Pause", [("InstanceID", "0")])


def stop(r: Renderer) -> None:
    soap_call(r, "Stop", [("InstanceID", "0")])


def transport_info(r: Renderer) -> Dict[str, str]:
    return soap_call(r, "GetTransportInfo", [("InstanceID", "0")], timeout=2.0)


def local_ip_for(host: str) -> str:
    """The Mac's address on the interface that reaches `host` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


# --------------------------------------------------------------------------- live stream

def ffmpeg_command(source: Optional[Path], fmt: str, bitrate: str) -> List[str]:
    """ffmpeg that emits an endless, real-time stream on stdout (like a turntable would)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
    if source is None:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    else:
        cmd += ["-stream_loop", "-1", "-i", str(source)]
    cmd += ["-map", "0:a:0", "-map_metadata", "-1", "-ac", "2", "-ar", "44100"]
    if fmt == "mp3":
        # No Xing/ID3 header: a live stream has no duration or tags up front.
        cmd += ["-c:a", "libmp3lame", "-b:a", bitrate, "-write_xing", "0",
                "-id3v2_version", "0", "-f", "mp3", "-"]
    elif fmt == "flac":
        cmd += ["-c:a", "flac", "-sample_fmt", "s16", "-f", "flac", "-"]
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return cmd


class Sources:
    """Allowed stream sources: the test tone, files in the media dir, files given on the CLI."""

    def __init__(self, media_dir: Path):
        self.media_dir = media_dir
        self.extra: Dict[str, Path] = {}

    def all(self) -> Dict[str, Optional[Path]]:
        found: Dict[str, Optional[Path]] = {TONE: None}
        if self.media_dir.is_dir():
            for p in sorted(self.media_dir.iterdir(), key=lambda p: p.name.lower()):
                if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file():
                    found[p.name] = p
        found.update(self.extra)
        return found

    def add(self, value: str) -> str:
        """Accept 'tone', a name in the media dir, or a path. Returns the source name."""
        if value in self.all():
            return value
        path = Path(value).expanduser()
        if not path.is_file():
            raise SystemExit(f"source not found: {value} (media dir: {self.media_dir})")
        self.extra[path.name] = path.resolve()
        return path.name


# --------------------------------------------------------------------------- app state

class Turntable:
    def __init__(self, sources: Sources, port: int, bitrate: str, use_didl: bool):
        self.sources, self.port, self.bitrate, self.use_didl = sources, port, bitrate, use_didl
        self.lock = threading.RLock()
        self.renderers: Dict[str, Renderer] = {}
        self.active: Optional[Renderer] = None
        self.source, self.fmt, self.last_action = TONE, "mp3", "idle"
        self.stream_url = ""
        self.connections: Dict[int, dict] = {}
        self.procs: Dict[int, subprocess.Popen] = {}
        self.events: deque = deque(maxlen=80)
        self._next_id = 0

    def event(self, msg: str, level: int = logging.INFO) -> None:
        log.log(level, msg)
        with self.lock:
            self.events.append({"t": time.strftime("%H:%M:%S"),
                                "level": logging.getLevelName(level), "msg": msg})

    # -- renderers
    def discover(self, timeout: float = 3.0) -> List[Renderer]:
        found = discover(timeout)
        with self.lock:
            self.renderers = {r.key: r for r in found}
        playable = sum(r.can_play for r in found)
        self.event(f"discovery: {len(found)} UPnP device(s), {playable} with AVTransport")
        return found

    def find(self, query: str) -> Renderer:
        if not self.renderers:
            self.discover()
        playable = [r for r in self.renderers.values() if r.can_play]
        if query in self.renderers:
            return self.renderers[query]
        exact = [r for r in playable if query in (r.host, r.name)]
        matches = exact or [r for r in playable if query.lower() in r.name.lower()]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(f"{r.name} ({r.host})" for r in matches or playable) or "none found"
        why = "ambiguous" if matches else "no renderer matches"
        raise LookupError(f"{why} {query!r}; candidates: {names}")

    # -- transport
    def url_for(self, r: Renderer, source: str, fmt: str) -> str:
        host = local_ip_for(r.host)
        return f"http://{host}:{self.port}/live/{quote(source, safe='')}/stream.{fmt}"

    def play(self, query: str, source: str, fmt: str) -> None:
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {', '.join(FORMATS)}")
        if source not in self.sources.all():
            raise ValueError(f"unknown source {source!r}")
        r = self.find(query)
        with self.lock:
            prev, same = self.active, (
                self.active is not None and self.active.key == r.key
                and self.source == source and self.fmt == fmt)
            resume = same and self.last_action == "pause"
        if resume:
            self._do("Play (resume)", play, r)
        else:
            if prev is not None and prev.key != r.key:
                self._try_stop(prev)
            url = self.url_for(r, source, fmt)
            metadata = didl_lite(f"Fake turntable: {source}", url, FORMATS[fmt]) \
                if self.use_didl else ""
            self.event(f"{r.name}: SetAVTransportURI {url}")
            self._do("SetAVTransportURI", lambda x: set_uri(x, url, metadata), r)
            self._do("Play", play, r)
            with self.lock:
                self.stream_url = url
        with self.lock:
            self.active, self.source, self.fmt, self.last_action = r, source, fmt, "play"

    def pause(self) -> None:
        r = self._require_active()
        self._do("Pause", pause, r)
        with self.lock:
            self.last_action = "pause"

    def stop(self) -> None:
        r = self._require_active()
        self._do("Stop", stop, r)
        with self.lock:
            self.last_action = "stop"

    def stop_on_exit(self) -> None:
        if self.active is not None and self.last_action in ("play", "pause"):
            self._try_stop(self.active)

    def _require_active(self) -> Renderer:
        if self.active is None:
            raise RuntimeError("nothing is playing yet - press Play first")
        return self.active

    def _do(self, label: str, fn, r: Renderer) -> None:
        try:
            fn(r)
        except UPnPError as exc:
            self.event(f"{r.name}: {exc}", logging.ERROR)
            raise
        self.event(f"{r.name}: {label} ok")

    def _try_stop(self, r: Renderer) -> None:
        try:
            stop(r)
            self.event(f"{r.name}: Stop ok")
        except UPnPError as exc:
            self.event(f"{r.name}: {exc}", logging.WARNING)

    def snapshot(self) -> dict:
        with self.lock:
            active = self.active
            state = {
                "active": asdict(active) if active else None,
                "source": self.source, "format": self.fmt, "last_action": self.last_action,
                "stream_url": self.stream_url,
                "connections": list(self.connections.values()),
                "events": list(self.events),
                "sources": list(self.sources.all()),
                "formats": list(FORMATS),
            }
        state["transport"] = None
        if active is not None:
            try:
                state["transport"] = transport_info(active)
            except UPnPError as exc:
                state["transport"] = {"error": str(exc)}
        return state

    # -- stream connections (one ffmpeg per renderer connection)
    def open_stream(self, client: str, source: str, fmt: str) -> Tuple[int, subprocess.Popen]:
        path = self.sources.all()[source]
        proc = subprocess.Popen(ffmpeg_command(path, fmt, self.bitrate),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self.lock:
            self._next_id += 1
            cid = self._next_id
            self.procs[cid] = proc
            self.connections[cid] = {"id": cid, "client": client, "source": source,
                                     "format": fmt, "started": time.time(), "bytes": 0}
        threading.Thread(target=self._drain_stderr, args=(cid, proc), daemon=True).start()
        self.event(f"stream #{cid} opened by {client}: {source} as {fmt}")
        return cid, proc

    def count_bytes(self, cid: int, n: int) -> None:
        with self.lock:
            self.connections[cid]["bytes"] += n

    def close_stream(self, cid: int, reason: str) -> None:
        with self.lock:
            proc = self.procs.pop(cid, None)
            conn = self.connections.pop(cid, None)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if conn:
            secs = time.time() - conn["started"]
            self.event(f"stream #{cid} closed after {secs:.0f}s, "
                       f"{conn['bytes'] / 1e6:.1f} MB: {reason}")

    def close_all_streams(self) -> None:
        for cid in list(self.procs):
            self.close_stream(cid, "shutting down")

    def _drain_stderr(self, cid: int, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            self.event(f"stream #{cid} ffmpeg: {line.decode('utf-8', 'replace').rstrip()}",
                       logging.ERROR)


# --------------------------------------------------------------------------- HTTP server

class Handler(BaseHTTPRequestHandler):
    server_version = "FakeTurntable/1.0"
    turntable: Turntable  # set on the class in make_server()

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
        elif path == "/api/state":
            self._json(self.turntable.snapshot())
        elif path == "/api/renderers":
            if "refresh" in self.path or not self.turntable.renderers:
                self.turntable.discover()
            self._json([dict(asdict(r), can_play=r.can_play)
                        for r in self.turntable.renderers.values()])
        elif path.startswith("/live/"):
            self._stream(path, head=False)
        else:
            self._send(404, "text/plain", b"not found\n")

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/live/"):
            self._stream(path, head=True)
        else:
            self._send(404, "text/plain", b"")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/play":
                self.turntable.play(body["renderer"], body["source"], body["format"])
            elif path == "/api/pause":
                self.turntable.pause()
            elif path == "/api/stop":
                self.turntable.stop()
            else:
                return self._send(404, "text/plain", b"not found\n")
        except (UPnPError, LookupError, ValueError, RuntimeError, KeyError) as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        self._json({"ok": True})

    def _stream(self, path: str, head: bool) -> None:
        parts = path.split("/")  # ['', 'live', '<source>', 'stream.<fmt>']
        fmt = parts[3].rsplit(".", 1)[-1] if len(parts) == 4 else ""
        source = unquote(parts[2]) if len(parts) == 4 else ""
        if fmt not in FORMATS or source not in self.turntable.sources.all():
            return self._send(404, "text/plain", b"unknown stream\n")
        # Live stream: no Content-Length, no ranges; HTTP/1.0, so the end is connection close.
        self.send_response(200)
        self.send_header("Content-Type", FORMATS[fmt])
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Accept-Ranges", "none")
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("contentFeatures.dlna.org", "DLNA.ORG_OP=00;DLNA.ORG_CI=0")
        self.send_header("Connection", "close")
        self.end_headers()
        if head:
            self.turntable.event(f"HEAD {path} from {self.client_address[0]}")
            return
        cid, proc = self.turntable.open_stream(self.client_address[0], source, fmt)
        reason = "ffmpeg ended"
        try:
            while True:
                chunk = proc.stdout.read1(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.turntable.count_bytes(cid, len(chunk))
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
            reason = f"client disconnected ({type(exc).__name__})"
        finally:
            self.turntable.close_stream(cid, reason)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, "application/json", json.dumps(data).encode())

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def make_server(turntable: Turntable, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"turntable": turntable})
    try:
        server = ThreadingHTTPServer(("", port), handler)
    except OSError as exc:
        raise SystemExit(f"cannot listen on port {port}: {exc} (try --port)") from None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --------------------------------------------------------------------------- web UI

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fake Turntable</title>
<style>
:root { --bg:#f6f5f2; --card:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#dedcd6;
        --accent:#c2410c; --ok:#15803d; --err:#b91c1c; color-scheme: light; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161615; --card:#21201e; --fg:#f2f1ee; --muted:#a19f99; --line:#3a3936;
          --accent:#fb923c; --ok:#4ade80; --err:#f87171; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width:640px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:20px; margin:0 0 4px; } .sub { color:var(--muted); margin:0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px; margin-bottom:14px; }
label { display:block; font-size:12px; text-transform:uppercase; letter-spacing:.05em;
        color:var(--muted); margin:0 0 6px; }
.row { display:flex; gap:8px; margin-bottom:14px; } .row:last-child { margin-bottom:0; }
select, button { font:inherit; color:inherit; border:1px solid var(--line);
                 border-radius:8px; padding:10px 12px; background:var(--bg); }
select { flex:1; min-width:0; }
button { cursor:pointer; } button:disabled { opacity:.5; cursor:default; }
.controls button { flex:1; font-weight:600; padding:14px; }
#play { background:var(--accent); border-color:var(--accent); color:#fff; }
.status { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; font-size:14px; }
.status dt { color:var(--muted); }
.status dd { margin:0; overflow-wrap:anywhere; white-space:pre-line; }
.state { font-weight:700; } .PLAYING { color:var(--ok); } .err { color:var(--err); }
#events { font:12px/1.5 ui-monospace, Menlo, monospace; max-height:240px; overflow:auto;
          margin:0; white-space:pre-wrap; }
#events .ERROR { color:var(--err); } #events .WARNING { color:var(--accent); }
#msg { min-height:1.4em; margin:10px 0 0; font-size:14px; }
</style></head><body><main>
<h1>Fake Turntable</h1>
<p class="sub">Stand-in for the Pro-Ject T2 W: live stream &rarr; UPnP renderer</p>

<div class="card">
  <label for="renderer">Speaker (UPnP renderer)</label>
  <div class="row">
    <select id="renderer"><option value="">Searching&hellip;</option></select>
    <button id="refresh" title="Search again">Search</button>
  </div>
  <label for="source">Source</label>
  <div class="row">
    <select id="source"></select>
    <select id="format" style="flex:0 0 auto"></select>
  </div>
  <div class="row controls">
    <button id="play">Play</button><button id="pause">Pause</button>
    <button id="stop">Stop</button>
  </div>
  <p id="msg"></p>
</div>

<div class="card">
  <dl class="status">
    <dt>Speaker</dt><dd id="s-renderer">&ndash;</dd>
    <dt>State</dt><dd><span id="s-state" class="state">&ndash;</span></dd>
    <dt>Stream</dt><dd id="s-url">&ndash;</dd>
    <dt>Pulling</dt><dd id="s-conns">no renderer connected to the stream</dd>
  </dl>
</div>

<div class="card"><label>Events</label><pre id="events"></pre></div>
</main>
<script>
const $ = id => document.getElementById(id);
let renderers = [], sourcesKey = "";

async function api(path, body) {
  const opts = body === undefined ? {} : {method: "POST", body: JSON.stringify(body)};
  const res = await fetch(path, opts);
  return res.json();
}

async function loadRenderers(refresh) {
  $("refresh").disabled = true;
  $("renderer").innerHTML = "<option value=''>Searching&hellip;</option>";
  try { renderers = await api("/api/renderers" + (refresh ? "?refresh=1" : "")); }
  finally { $("refresh").disabled = false; }
  const playable = renderers.filter(r => r.can_play);
  const sel = $("renderer"), prev = localStorage.getItem("renderer");
  sel.innerHTML = "";
  if (!playable.length) sel.innerHTML = "<option value=''>No UPnP renderer found</option>";
  for (const r of playable) {
    const o = new Option(`${r.name} (${r.host})` + (r.model ? ` - ${r.model}` : ""), r.key);
    o.selected = r.key === prev; sel.add(o);
  }
}

function fillSources(state) {
  const key = state.sources.join("|");
  if (key === sourcesKey) return;
  sourcesKey = key;
  const sel = $("source"), prev = localStorage.getItem("source") || state.source;
  sel.innerHTML = "";
  for (const s of state.sources)
    sel.add(new Option(s === "tone" ? "Test tone (440 Hz)" : s, s, false, s === prev));
  const fsel = $("format");
  if (!fsel.options.length) {
    for (const f of state.formats) fsel.add(new Option(f.toUpperCase(), f));
    fsel.value = localStorage.getItem("format") || state.format;
  }
}

function render(state) {
  fillSources(state);
  const t = state.transport, a = state.active;
  $("s-renderer").textContent = a ? `${a.name} (${a.host})` : "-";
  const st = $("s-state");
  if (t && t.error) { st.textContent = t.error; st.className = "state err"; }
  else if (t) {
    st.textContent = t.CurrentTransportState || "?";
    st.className = "state " + (t.CurrentTransportState || "");
  } else { st.textContent = "-"; st.className = "state"; }
  $("s-url").textContent = state.stream_url || "-";
  $("s-conns").textContent = state.connections.length
    ? state.connections.map(c => `#${c.id} ${c.client}: ${c.format}, ` +
        `${Math.round(Date.now() / 1000 - c.started)}s, ${(c.bytes / 1e6).toFixed(1)} MB`
      ).join("\\n")
    : "no renderer connected to the stream";
  const ev = $("events");
  ev.innerHTML = "";
  for (const e of state.events.slice().reverse()) {
    const line = document.createElement("div");
    line.className = e.level; line.textContent = `${e.t}  ${e.msg}`; ev.appendChild(line);
  }
}

async function poll() {
  try { render(await api("/api/state")); } catch (e) { $("msg").textContent = "Server gone?"; }
}

async function action(name, body) {
  $("msg").textContent = "..."; $("msg").className = "";
  const res = await api("/api/" + name, body || {});
  $("msg").textContent = res.ok ? "" : res.error;
  $("msg").className = res.ok ? "" : "err";
  poll();
}

$("refresh").onclick = () => loadRenderers(true);
$("renderer").onchange = e => localStorage.setItem("renderer", e.target.value);
$("source").onchange = e => {
  localStorage.setItem("source", e.target.value);
  const ext = e.target.value.split(".").pop().toLowerCase();
  if (ext === "mp3" || ext === "flac") {
    $("format").value = ext; localStorage.setItem("format", ext);
  }
};
$("format").onchange = e => localStorage.setItem("format", e.target.value);
$("play").onclick = () => {
  if (!$("renderer").value) { $("msg").textContent = "Pick a speaker first"; return; }
  action("play", {renderer: $("renderer").value, source: $("source").value,
                  format: $("format").value});
};
$("pause").onclick = () => action("pause");
$("stop").onclick = () => action("stop");

loadRenderers(false); poll(); setInterval(poll, 2000);
</script></body></html>
"""


# --------------------------------------------------------------------------- CLI

def print_renderers(found: List[Renderer]) -> None:
    if not found:
        print("No UPnP devices answered. Check System Settings > Privacy & Security > "
              "Local Network for Terminal / VS Code.")
        return
    for r in found:
        print(f"{r.name}")
        print(f"    IP: {r.host}   model: {r.manufacturer} {r.model}".rstrip())
        print(f"    AVTransport: {'yes' if r.can_play else 'no'}")
        print(f"    description: {r.location}")


def lan_urls(port: int) -> List[str]:
    urls = [f"http://localhost:{port}"]
    try:
        urls.append(f"http://{local_ip_for('192.0.2.1')}:{port}")  # TEST-NET; nothing is sent
    except OSError:
        pass
    return urls


def _raise_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Stand-in for the Pro-Ject T2 W: live MP3/FLAC stream to a UPnP renderer. "
                    "Without --list/--renderer it starts the web UI.")
    p.add_argument("--list", action="store_true", help="discover UPnP devices and exit")
    p.add_argument("--renderer", metavar="NAME|IP", help="play on this renderer (no web UI)")
    p.add_argument("--stop", action="store_true", help="with --renderer: send Stop and exit")
    p.add_argument("--source", default=TONE,
                   help="'tone', a file name in the media dir, or a path (default: tone)")
    p.add_argument("--format", choices=list(FORMATS), default="mp3", help="default: mp3")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default: {DEFAULT_PORT}")
    p.add_argument("--media-dir", type=Path, default=DEFAULT_MEDIA_DIR,
                   help="folder with audio files (default: tools/media)")
    p.add_argument("--bitrate", default="320k", help="MP3 bitrate (default: 320k)")
    p.add_argument("--no-didl", action="store_true",
                   help="send empty CurrentURIMetaData (for picky renderers)")
    p.add_argument("--timeout", type=float, default=3.0, help="discovery seconds (default: 3)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    if args.list:
        print_renderers(discover(args.timeout))
        return 0

    sources = Sources(args.media_dir)
    source = sources.add(args.source)
    tt = Turntable(sources, args.port, args.bitrate, use_didl=not args.no_didl)

    if args.renderer and args.stop:
        tt.discover(args.timeout)
        r = tt.find(args.renderer)
        try:
            stop(r)
        except UPnPError as exc:
            log.error("%s: %s", r.name, exc)
            return 1
        log.info("%s: Stop ok", r.name)
        return 0

    signal.signal(signal.SIGTERM, _raise_interrupt)  # same clean stop as Ctrl+C
    server = make_server(tt, args.port)
    try:
        if args.renderer:
            tt.discover(args.timeout)
            tt.play(args.renderer, source, args.format)
            log.info("playing - Ctrl+C to stop")
        else:
            log.info("web UI: %s  (Ctrl+C to quit)", "  ".join(lan_urls(args.port)))
        while True:
            time.sleep(1)
    except (UPnPError, LookupError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        tt.stop_on_exit()
        server.shutdown()
        tt.close_all_streams()
    return 0


if __name__ == "__main__":
    sys.exit(main())
