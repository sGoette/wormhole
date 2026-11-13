"""Web UI: pick OwnTone speakers, set each speaker's volume, start/stop the PCM feed.

Runs on the Mac (Phase 3) and on the NAS (grows into the Phase 6 controller).
Listens on all interfaces so an iPhone on the LAN can use it.
"""

import json
import logging
import signal
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

from . import config, feed
from .owntone import OwnTone, OwnToneError, log_problems_since

log = logging.getLogger(__name__)

MEDIA_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus"}


class FeedControl:
    """Runs one feed.Feeder in a background thread."""

    def __init__(self, cfg: Dict[str, str], factory: Callable[..., feed.Feeder] = feed.Feeder):
        self.cfg, self.factory = cfg, factory
        self.lock = threading.Lock()
        self.feeder: Optional[feed.Feeder] = None
        self.thread: Optional[threading.Thread] = None
        self.source = ""
        self.origin = ""  # who started it: "web" or "upnp"

    def presets(self) -> List[str]:
        items = [feed.TONE]
        for value in [self.cfg.get("FEED_INPUT", "")] + self.cfg.get("FEED_PRESETS", "").split(","):
            if value.strip():
                items.append(value.strip())
        media = config.ROOT / "tools" / "media"  # Mac only; absent on the NAS
        if media.is_dir():
            items += sorted(f"tools/media/{p.name}" for p in media.iterdir()
                            if p.suffix.lower() in MEDIA_EXTENSIONS)
        return list(dict.fromkeys(items))  # de-duplicate, keep order

    def resolve(self, source: str) -> str:
        """Allow 'tone', http(s) URLs and existing files; relative paths are from ROOT."""
        source = source.strip()
        if source == feed.TONE or source.startswith(("http://", "https://")):
            return source
        path = Path(source).expanduser()
        path = path if path.is_absolute() else config.ROOT / path
        if not path.is_file():
            raise ValueError(f"not 'tone', an http(s) URL or an existing file: {source}")
        return str(path)

    def start(self, source: str, origin: str = "web",
              on_first_data: Optional[Callable[[], None]] = None) -> None:
        resolved = self.resolve(source)
        self.stop()
        feeder = self.factory(
            source=resolved,
            pipe=config.path(self.cfg, "PCM_PIPE"),
            rate=int(self.cfg.get("PIPE_SAMPLE_RATE", "44100")),
            bits=int(self.cfg.get("PIPE_BITS", "16")),
            on_first_data=on_first_data,
        )
        thread = threading.Thread(target=feeder.run, name="feed", daemon=True)
        with self.lock:
            self.feeder, self.thread = feeder, thread
            self.source, self.origin = source.strip(), origin
        thread.start()

    def owned_by(self, origin: str) -> bool:
        """True while a feed started by `origin` is running."""
        state = self.state()
        return state["running"] and state["origin"] == origin

    def stop(self, origin: Optional[str] = None) -> None:
        """Stop the feed; with `origin`, only if that origin started it."""
        with self.lock:
            feeder, thread = self.feeder, self.thread
            if origin is not None and self.origin != origin:
                return
        if feeder is not None:
            feeder.stop()
        if thread is not None:
            thread.join(timeout=5)

    def state(self) -> dict:
        with self.lock:
            feeder, thread, source, origin = self.feeder, self.thread, self.source, self.origin
        running = bool(thread and thread.is_alive() and feeder and not feeder.stopping)
        return {
            "running": running,
            "source": source,
            "origin": origin,
            "seconds": int(time.time() - feeder.started_at)
            if running and feeder and feeder.started_at else 0,
            "restarts": feeder.restarts if feeder else 0,
            "last_error": feeder.last_error if feeder else "",
            "bytes": feeder.bytes_written if feeder else 0,
            "flowing": bool(running and feeder.flowing()),
        }


class PinRequired(OwnToneError):
    """The device wants PIN pairing; the UI asks for the PIN instead of showing an error."""


# Known causes for messages in OwnTone's log -> what to do about them.
HINTS = [
    ("GET /info", "HomePod software 27 rejects OwnTone's default User-Agent. Set "
                  'user_agent = "AirPlay/999.0.0" in the general section of owntone.conf '
                  "(OwnTone issue #2042). If that is set: Apple Home app > Allow Speakers & "
                  "TV Access > Anyone On the Same Network."),
    ("ANNOUNCE request failed", "Apple Home app > Allow Speakers & TV Access > Anyone On the "
                                "Same Network (OwnTone docs, audio-outputs/airplay.md)."),
]


class App:
    def __init__(self, cfg: Dict[str, str], feed_control: Optional[FeedControl] = None):
        self.owntone = OwnTone(config.require(cfg, "OWNTONE_URL"), timeout=3)
        self.feed = feed_control or FeedControl(cfg)
        self.owntone_log = config.path(cfg, "OWNTONE_LOG") if cfg.get("OWNTONE_LOG") else None
        self.renderer = None  # renderer.RendererService, when RENDERER_NAME is set
        self.pipe_name = config.path(cfg, "PCM_PIPE").name if cfg.get("PCM_PIPE") else "turntable"

    def explain(self, exc: OwnToneError, since: float) -> str:
        """Add the reason from OwnTone's log (and a hint if we know the cause)."""
        if self.owntone_log is None:
            return str(exc)
        problems = log_problems_since(self.owntone_log, since)
        msg = str(exc)
        if problems:
            msg += "\nOwnTone log: " + " | ".join(problems)
        for needle, hint in HINTS:
            if any(needle in p for p in problems):
                msg += "\nHint: " + hint
                break
        return msg

    def state(self) -> dict:
        out: dict = {"feed": self.feed.state(), "presets": self.feed.presets(),
                     "outputs": [], "player": None, "error": "",
                     "renderer": self.renderer.renderer.status() if self.renderer else None}
        try:
            outputs = sorted(self.owntone.outputs(), key=lambda o: o.name.lower())
            out["outputs"] = [asdict(o) for o in outputs]
            out["player"] = self.owntone.player()
        except OwnToneError as exc:
            out["error"] = str(exc)
        return out

    def update_output(self, output_id: str, body: dict) -> None:
        volume = body.get("volume")
        if volume is not None and not (isinstance(volume, int) and 0 <= volume <= 100):
            raise ValueError("volume must be an integer 0-100")
        selected = body.get("selected")
        if selected is not None and not isinstance(selected, bool):
            raise ValueError("selected must be true or false")
        if "pin" in body:
            raise ValueError("use POST /api/outputs/<id>/pair to send a PIN")
        since = time.time()
        try:
            self.owntone.update_output(output_id, selected=selected, volume=volume)
        except OwnToneError as exc:
            if selected and self._needs_pin(output_id):
                # Expected: OwnTone started pairing and the device now shows a PIN.
                raise PinRequired("The device shows a PIN on its screen: enter it and "
                                  "press Pair.") from None
            raise OwnToneError(self.explain(exc, since)) from None

    def pair_output(self, output_id: str, pin: str) -> None:
        """PIN pairing as OwnTone expects it (maintainer, issue #1967): switching on started
        pairing and the device shows a PIN; send the PIN on its own, then switch on again."""
        pin = pin.strip()
        if not pin:
            raise ValueError("Enter the PIN shown on the device first.")
        since = time.time()
        try:
            self.owntone.update_output(output_id, pin=pin)
        except OwnToneError as exc:
            raise OwnToneError("Pairing failed. " + self.explain(exc, since)) from None
        try:
            self.owntone.update_output(output_id, selected=True)
        except OwnToneError as exc:
            if self._needs_pin(output_id):
                raise PinRequired("PIN not accepted. If the device now shows a new PIN, "
                                  "enter that one.") from None
            raise OwnToneError(self.explain(exc, since)) from None

    def _needs_pin(self, output_id: str) -> bool:
        try:
            out = self.owntone.output(output_id)
        except OwnToneError:
            return False
        return out.needs_auth_key or out.requires_auth

    def start_feed(self, source: str) -> None:
        """Start the feed; OwnTone is told to play the pipe once the first audio is there
        (autostart is not reliable after a stop). Failures show up as the feed's last_error."""
        self.feed.start(source, on_first_data=lambda: self.owntone.play_pipe(self.pipe_name))

    def stop_all(self) -> None:
        self.feed.stop()
        self.owntone.stop()


def make_handler(app: App) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "wormhole"

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif path == "/api/state":
                self._json(app.state())
            else:
                self._send(404, "text/plain", b"not found\n")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_POST(self) -> None:
            self._handle("POST")

        def _handle(self, method: str) -> None:
            path = urlsplit(self.path).path
            try:
                body = self._body()
                if method == "POST" and path.startswith("/api/outputs/") \
                        and path.endswith("/pair"):
                    app.pair_output(path.split("/")[3], str(body.get("pin", "")))
                elif method == "PUT" and path.startswith("/api/outputs/"):
                    app.update_output(path.rsplit("/", 1)[1], body)
                elif method == "POST" and path == "/api/feed/start":
                    app.start_feed(str(body.get("source", "")))
                elif method == "POST" and path == "/api/feed/stop":
                    app.feed.stop()
                elif method == "POST" and path == "/api/stop":
                    app.stop_all()
                else:
                    return self._send(404, "text/plain", b"not found\n")
            except PinRequired as exc:
                return self._json({"ok": False, "needs_pin": True, "error": str(exc)}, 400)
            except (OwnToneError, ValueError, SystemExit) as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            self._json({"ok": True})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def _json(self, data, status: int = 200) -> None:
            self._send(status, "application/json", json.dumps(data).encode())

        def _send(self, status: int, ctype: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _raise_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def serve(cfg: Dict[str, str], port: int) -> int:
    from .renderer import QuietHTTPServer
    app = App(cfg)
    try:
        server = QuietHTTPServer(("", port), make_handler(app))
    except OSError as exc:
        raise SystemExit(f"cannot listen on port {port}: {exc}") from None
    # SIGTERM (service stop on the NAS) gets the same clean shutdown as Ctrl+C.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    if cfg.get("RENDERER_NAME"):
        from .renderer import RendererService
        try:
            app.renderer = RendererService(cfg["RENDERER_NAME"],
                                           int(cfg.get("RENDERER_PORT", "49494")),
                                           app.feed, app.owntone,
                                           host_ip=cfg.get("RENDERER_IP") or None,
                                           pipe_name=app.pipe_name)
        except OSError as exc:
            raise SystemExit(f"cannot start UPnP renderer: {exc}") from None
        app.renderer.start()
    log.info("web UI on http://localhost:%d (OwnTone: %s)", port, cfg.get("OWNTONE_URL"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        if app.renderer is not None:
            app.renderer.stop()  # SSDP byebye, so control points drop us right away
        app.feed.stop()
        server.server_close()
    return 0


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wormhole</title>
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
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
     margin:0 0 10px; font-weight:600; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px; margin-bottom:14px; }
button, input[type=text] { font:inherit; color:inherit; border:1px solid var(--line);
        border-radius:8px; padding:10px 12px; background:var(--bg); }
button { cursor:pointer; font-weight:600; } button:disabled { opacity:.5; cursor:default; }
.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.row { display:flex; gap:8px; flex-wrap:wrap; } .row > input { flex:1; min-width:0; }
.spk { display:grid; grid-template-columns:auto 1fr auto; gap:4px 12px; align-items:center;
       padding:12px 0; border-top:1px solid var(--line); }
.spk:first-child { border-top:0; padding-top:0; }
.spk .name { font-weight:600; } .spk .meta { color:var(--muted); font-size:13px; }
.spk input[type=range] { grid-column:2 / 3; width:100%; accent-color:var(--accent); }
.spk .vol { font-variant-numeric:tabular-nums; color:var(--muted); min-width:3ch;
            text-align:right; }
.spk.off .name { color:var(--muted); } .spk.off input[type=range] { opacity:.4; }
.spk .pin { grid-column:2 / 4; }
.spk .pin .note { font-size:13px; color:var(--muted); margin:4px 0 6px; }
.spk .pin .note.ask { color:var(--accent); font-weight:600; }
.spk .pin .note.err { color:var(--err); }
.sw { position:relative; width:44px; height:26px; flex:none; }
.sw input { opacity:0; width:100%; height:100%; margin:0; cursor:pointer; position:absolute; }
.sw span { position:absolute; inset:0; border-radius:13px; background:var(--line);
           pointer-events:none; transition:background .15s; }
.sw span::after { content:""; position:absolute; left:3px; top:3px; width:20px; height:20px;
                  border-radius:50%; background:#fff; transition:transform .15s; }
.sw input:checked + span { background:var(--ok); }
.sw input:checked + span::after { transform:translateX(18px); }
.status { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; font-size:14px;
          margin-top:12px; }
.status dt { color:var(--muted); } .status dd { margin:0; overflow-wrap:anywhere; }
.good { color:var(--ok); font-weight:600; } .err { color:var(--err); }
#msg { min-height:1.4em; margin:0 0 12px; font-size:14px; white-space:pre-line; }
</style></head><body><main>
<h1>Wormhole</h1>
<p class="sub">Turntable &rarr; UPnP renderer &rarr; pipe &rarr; OwnTone &rarr; HomePods</p>
<p id="conn" class="err" hidden></p>
<p id="msg"></p>

<div class="card">
  <h2>Speakers</h2>
  <div id="speakers"><span class="sub">Loading&hellip;</span></div>
</div>

<div class="card">
  <h2>Feed into the pipe</h2>
  <div class="row">
    <input type="text" id="source" list="presets" placeholder="tone, file or stream URL">
    <datalist id="presets"></datalist>
    <button id="start" class="primary">Start</button>
    <button id="stopfeed">Stop</button>
  </div>
  <dl class="status">
    <dt>Feed</dt><dd id="f-state">-</dd>
    <dt>OwnTone</dt><dd id="p-state">-</dd>
    <dt>Renderer</dt><dd id="r-state">-</dd>
  </dl>
</div>

<button id="stopall" style="width:100%">Stop everything</button>
</main>
<script>
const $ = id => document.getElementById(id);
const rows = {};
let presetsKey = "", busy = false;

async function call(method, path, body) {
  const res = await fetch(path, {method, body: body ? JSON.stringify(body) : undefined});
  const data = await res.json();
  if (!data.ok && data.error) {
    const err = new Error(data.error);
    err.needsPin = !!data.needs_pin;
    throw err;
  }
  return data;
}

function flash(text, isErr) {
  $("msg").textContent = text || "";
  $("msg").className = isErr ? "err" : "";
}

async function act(method, path, body) {
  try { await call(method, path, body); flash(""); }
  catch (e) { flash(e.message, true); }
  poll();
}

function askPin(r, message) {
  r.asking = true;
  r.pin.hidden = false;
  r.note.textContent = message;
  r.note.className = "note ask";
  r.pinInput.value = "";
  r.pairBtn.disabled = true;
  r.pinInput.focus();
  flash("");
}

function makeRow(o) {
  const el = document.createElement("div");
  el.className = "spk";
  el.innerHTML = `<label class="sw"><input type="checkbox"><span></span></label>
    <div><div class="name"></div><div class="meta"></div></div><span class="vol"></span>
    <span></span><input type="range" min="0" max="100" step="1"><span></span>
    <div class="pin" hidden>
      <div class="note">Needs PIN pairing: switch it on, then enter the PIN it shows.</div>
      <div class="row">
        <input type="text" inputmode="numeric" autocomplete="off"
               placeholder="PIN shown on the device">
        <button disabled>Pair</button></div></div>`;
  const q = sel => el.querySelector(sel);
  const r = {el, box: q("input[type=checkbox]"), range: q("input[type=range]"),
             name: q(".name"), meta: q(".meta"), vol: q(".vol"), pin: q(".pin"),
             note: q(".pin .note"), pinInput: q(".pin input"), pairBtn: q(".pin button"),
             asking: false, dragging: false, timer: null};
  // Switching on a PIN device makes it show a PIN: ask for it instead of showing an error.
  r.box.onchange = async () => {
    try { await call("PUT", "/api/outputs/" + o.id, {selected: r.box.checked}); flash(""); }
    catch (e) {
      if (e.needsPin) askPin(r, e.message); else flash(e.message, true);
    }
    poll();
  };
  r.pinInput.oninput = () => { r.pairBtn.disabled = !r.pinInput.value.trim(); };
  r.pinInput.onkeydown = e => { if (e.key === "Enter" && !r.pairBtn.disabled) r.pairBtn.click(); };
  r.pairBtn.onclick = async () => {
    r.pairBtn.disabled = true;
    r.note.textContent = "Pairing…";
    try {
      await call("POST", `/api/outputs/${o.id}/pair`, {pin: r.pinInput.value});
      r.asking = false;
      r.note.textContent = "Paired and switched on.";
      r.pinInput.value = "";
      flash("");
    } catch (e) {
      if (e.needsPin) askPin(r, e.message);
      else { r.note.textContent = e.message; r.note.className = "note err"; }
    }
    r.pairBtn.disabled = !r.pinInput.value.trim();
    poll();
  };
  const sendVolume = () => act("PUT", "/api/outputs/" + o.id, {volume: Number(r.range.value)});
  r.range.oninput = () => {
    r.dragging = true; r.vol.textContent = r.range.value;
    clearTimeout(r.timer); r.timer = setTimeout(sendVolume, 200);
  };
  r.range.onchange = () => { clearTimeout(r.timer); sendVolume(); r.dragging = false; };
  return r;
}

function renderSpeakers(outputs) {
  const list = $("speakers"), seen = new Set();
  if (!outputs.length) { list.innerHTML = "<span class='sub'>No speakers found</span>"; return; }
  if (!Object.keys(rows).length) list.innerHTML = "";
  outputs.forEach((o, i) => {
    seen.add(o.id);
    const r = rows[o.id] || (rows[o.id] = makeRow(o));
    // Move a row only when it is out of place: moving a node in the DOM drops the focus of
    // any input inside it (e.g. the PIN field being typed into).
    if (list.children[i] !== r.el) list.insertBefore(r.el, list.children[i] || null);
    r.el.classList.toggle("off", !o.selected);
    r.box.checked = o.selected;
    r.name.textContent = o.name;
    const flags = [o.type];
    if (o.needs_auth_key || o.requires_auth) flags.push("needs PIN");
    r.meta.textContent = flags.join(" · ");
    r.pin.hidden = !(o.needs_auth_key || o.requires_auth || r.asking);
    if (!r.dragging) { r.range.value = o.volume; r.vol.textContent = o.volume; }
  });
  for (const id of Object.keys(rows)) if (!seen.has(id)) { rows[id].el.remove(); delete rows[id]; }
}

function render(s) {
  $("conn").hidden = !s.error;
  $("conn").textContent = s.error;
  if (!s.error) renderSpeakers(s.outputs);
  const key = s.presets.join("|");
  if (key !== presetsKey) {
    presetsKey = key;
    $("presets").innerHTML = "";
    for (const p of s.presets) $("presets").appendChild(new Option(p));
    if (!$("source").value) $("source").value = s.feed.source || s.presets[0];
  }
  const f = s.feed, fs = $("f-state");
  const by = f.origin === "upnp" ? "UPnP" : "web UI";
  const flow = f.flowing ? `audio flowing, ${(f.bytes / 1e6).toFixed(1)} MB` : "no audio yet";
  fs.textContent = f.running ? `${flow}, ${f.seconds}s (${by}): ${f.source}` : "stopped";
  fs.className = f.flowing ? "good" : "";
  if (f.restarts) fs.textContent += ` (restarts: ${f.restarts}; last: ${f.last_error})`;
  const p = s.player;
  $("p-state").textContent = p ? `${p.state}, master volume ${p.volume}` : "-";
  $("p-state").className = p && p.state === "play" ? "good" : "";
  const r = s.renderer;
  $("r-state").textContent = !r ? "off (set RENDERER_NAME)"
    : `"${r.name}": ${r.state}` + (r.controller ? ` (controlled from ${r.controller})` : "");
  $("r-state").className = r && r.state === "PLAYING" ? "good" : "";
}

async function poll() {
  if (busy) return;
  busy = true;
  try { render(await (await fetch("/api/state")).json()); }
  catch (e) { $("conn").hidden = false; $("conn").textContent = "Web UI server not reachable"; }
  finally { busy = false; }
}

$("start").onclick = () => act("POST", "/api/feed/start", {source: $("source").value});
$("stopfeed").onclick = () => act("POST", "/api/feed/stop");
$("stopall").onclick = () => act("POST", "/api/stop");
poll(); setInterval(poll, 1500);
</script></body></html>
"""
