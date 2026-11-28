"""Web UI backend: JSON API for speakers, player, records, renderer name and updates.

The page itself is static (src/wormhole/frontend/), served by httpserver.StaticFiles.
Listens on all interfaces so an iPhone on the LAN can use it.
"""

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import config, covers
from .feed import FeedControl
from .settings import Settings, clean_renderer_name
from .httpserver import (HTTPApp, HTTPError, QuietHTTPServer, Request, Response, Router,
                         StaticFiles, json_response)
from .owntone import OwnTone, OwnToneError, log_problems_since
from .pipemeta import MetadataPipe
from .records import Record, Records
from .update import UpdateError, Updater

log = logging.getLogger(__name__)

FRONTEND = Path(__file__).resolve().parent / "frontend"


class PinRequired(OwnToneError):
    """The device wants PIN pairing; the UI asks for the PIN instead of showing an error."""


# Known causes for messages in OwnTone's log -> what to do about them.
HINTS = [
    ("GET /info", "HomePod software 27 rejects OwnTone's default User-Agent. Set "
                  'user_agent = "AirPlay/999.0.0" in the airplay_shared section of owntone.conf '
                  "(OwnTone issue #2042). If that is set: Apple Home app > Allow Speakers & "
                  "TV Access > Anyone On the Same Network."),
    ("ANNOUNCE request failed", "Apple Home app > Allow Speakers & TV Access > Anyone On the "
                                "Same Network (OwnTone docs, audio-outputs/airplay.md)."),
]


class App:
    """What the API does; routes() maps URLs onto it."""

    def __init__(self, cfg: Dict[str, str], feed_control: Optional[FeedControl] = None,
                 renderer_factory: Optional[Callable[..., Any]] = None):
        self.cfg = cfg
        self.owntone = OwnTone(config.require(cfg, "OWNTONE_URL"), timeout=3,
                               artwork=config.optional_path(cfg, "NOW_PLAYING_ARTWORK"),
                               title=cfg.get("NOW_PLAYING_TITLE", ""),
                               artist=cfg.get("NOW_PLAYING_ARTIST", ""))
        # "Now playing" without a record chosen: what the env file says.
        self.default_now_playing = (self.owntone.title, self.owntone.artist,
                                    self.owntone.artwork)
        self.feed = feed_control or FeedControl(cfg)
        self.owntone_log = config.path(cfg, "OWNTONE_LOG") if cfg.get("OWNTONE_LOG") else None
        self.settings = Settings(config.path(cfg, "SETTINGS_FILE")
                                 if cfg.get("SETTINGS_FILE") else None)
        self.renderer = None  # renderer.RendererService, once start_renderer() ran
        self.renderer_factory = renderer_factory
        self.pipe_name = config.path(cfg, "PCM_PIPE").name if cfg.get("PCM_PIPE") else "turntable"
        self.updater = Updater(remote=cfg.get("UPDATE_REMOTE", "origin"),
                               branch=cfg.get("UPDATE_BRANCH", "main"))
        self.last_check: Optional[dict] = None
        self.request_restart: Callable[[], None] = lambda: None  # set by serve()
        # Record library (records.py) and where its covers come from (covers.py).
        self.records = Records(config.path(cfg, "RECORDS_DIR")) \
            if cfg.get("RECORDS_DIR") else None
        self.cover_sources = covers.configured(cfg)
        self.meta_pipe = MetadataPipe.for_pcm_pipe(config.path(cfg, "PCM_PIPE")) \
            if cfg.get("PCM_PIPE") else None
        self.now_playing: Optional[Record] = None
        self._restore_now_playing()

    # -- software updates (see update.py)
    def update_status(self) -> dict:
        try:
            status = self.updater.current()
        except UpdateError as exc:
            return {"available": False, "reason": str(exc)}
        status["last_check"] = self.last_check
        return status

    def check_update(self) -> dict:
        try:
            self.last_check = self.updater.check()
        except UpdateError as exc:
            raise ValueError(f"Update check failed: {exc}") from None
        return self.last_check

    def apply_update(self, rollback: bool = False) -> dict:
        try:
            result = self.updater.rollback() if rollback else self.updater.apply()
        except UpdateError as exc:
            raise ValueError(f"{'Rollback' if rollback else 'Update'} failed: {exc}") from None
        self.last_check = None
        self.request_restart()  # re-exec with the new code once the response is sent
        return result

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

    # -- UPnP renderer (renderer.py)
    def renderer_name(self) -> str:
        """Name saved from the web UI, else RENDERER_NAME; empty = no renderer."""
        return self.settings.get("renderer_name") or self.cfg.get("RENDERER_NAME", "")

    def start_renderer(self, name: str) -> None:
        factory = self.renderer_factory
        if factory is None:
            from .renderer import RendererService as factory
        self.renderer = factory(name, int(self.cfg.get("RENDERER_PORT", "49494")),
                                self.feed, self.owntone,
                                host_ip=self.cfg.get("RENDERER_IP") or None,
                                pipe_name=self.pipe_name,
                                lossless_port=int(self.cfg.get("LOSSLESS_STREAM_PORT") or 0)
                                or None)
        self.renderer.start()

    def set_renderer_name(self, name: Any) -> dict:
        name = clean_renderer_name(name)
        self.settings.set("renderer_name", name)
        if self.renderer is None:
            try:
                self.start_renderer(name)
            except OSError as exc:
                raise ValueError(f"Saved, but the renderer could not start: {exc}") from None
        elif self.renderer.renderer.name != name:
            self.renderer.rename(name)
        return dict(self.renderer.renderer.status(), ok=True)

    def state(self) -> dict:
        out: dict = {"outputs": [], "player": None, "error": "",
                     "renderer": self.renderer.renderer.status() if self.renderer else None,
                     # what ffmpeg decodes (codec, rate, bits, lossless), from its own log
                     "input": self.feed.state()["input"],
                     # the record chosen in the Records tab, shown in the player bar
                     "now_playing": self.now_playing.to_json() if self.now_playing else None}
        if out["renderer"] is not None:
            # Shown in the player bar. The turntable doesn't announce a name of its own
            # (no SSDP answer, 2026-11-24), so it is the "now playing" title.
            out["renderer"]["source"] = self.cfg.get("NOW_PLAYING_TITLE", "")
        try:
            outputs = sorted(self.owntone.outputs(), key=lambda o: o.name.lower())
            hidden = self.hidden_outputs()
            out["outputs"] = [dict(asdict(o), hidden=o.name in hidden) for o in outputs]
            out["player"] = self.owntone.player()
        except OwnToneError as exc:
            out["error"] = str(exc)
        return out

    # -- hidden speakers (settings overlay): kept by name, which survives OwnTone db resets
    def hidden_outputs(self) -> List[str]:
        names = self.settings.get("hidden_outputs", [])
        return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []

    def set_hidden(self, output_id: str, hidden: Any) -> None:
        if not isinstance(hidden, bool):
            raise ValueError("hidden must be true or false")
        out = self.owntone.output(output_id)
        names = [n for n in self.hidden_outputs() if n != out.name]
        if hidden:
            names = sorted(names + [out.name], key=str.lower)
            if out.selected:
                # A hidden speaker that keeps playing could not be switched off any more.
                self.owntone.update_output(output_id, selected=False)
        self.settings.set("hidden_outputs", names)

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

    # -- player bar: master volume, play / pause
    def player_volume(self, body: dict) -> None:
        volume = body.get("volume")
        if not (isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100):
            raise ValueError("volume must be an integer 0-100")
        self.owntone.set_master_volume(volume)

    def player_play(self) -> None:
        """(Re)connect to the turntable's live stream: a plain OwnTone play would first play
        what queued up during a pause, and stay that far behind; after a Stop there is no
        stream at all."""
        if self.renderer is not None and self.renderer.renderer.play_stream():
            return
        self.owntone.play()

    def player_pause(self) -> None:
        self.owntone.pause()

    def player_stop(self) -> None:
        """The turntable keeps streaming (silence) after the record ends, until its 20-minute
        sleep timer: Stop ends playback here, so the speakers and the Apple TV let go."""
        if self.renderer is None or not self.renderer.renderer.stop_stream():
            self.owntone.stop()  # something else was playing: stop it and clear the queue
        # Stop means "done listening": the next record starts as "Turntable" again.
        self.set_now_playing(None)

    # -- record library (Records tab)
    def library(self) -> Records:
        if self.records is None:
            raise ValueError("RECORDS_DIR is not set, so there is no record library "
                             "(server: re-run install.sh)")
        return self.records

    def source(self, name: Any) -> covers.CoverSource:
        if not self.cover_sources:
            raise ValueError("No cover source configured (COVER_SOURCES).")
        if not name:
            return next(iter(self.cover_sources.values()))
        if name not in self.cover_sources:
            raise ValueError(f"Unknown cover source {name!r}.")
        return self.cover_sources[name]

    def list_records(self) -> dict:
        return {"records": [r.to_json() for r in self.library().all()],
                "current": self.now_playing.id if self.now_playing else None,
                "sources": [{"id": s.name, "label": s.label}
                            for s in self.cover_sources.values()]}

    def search_records(self, query: Dict[str, List[str]]) -> dict:
        def first(key: str) -> str:
            return (query.get(key) or [""])[0].strip()
        src = self.source(first("source"))
        try:
            results = src.search(first("album"), first("artist"))
        except covers.CoverError as exc:
            raise ValueError(str(exc)) from None
        library = self.library()
        out = []
        for album in results:
            known = library.find(album.source, album.ref)
            out.append(dict(album.to_json(), in_library=known.id if known else None))
        return {"source": src.name, "results": out}

    def add_record(self, body: dict) -> dict:
        """From a search result ({source, ref, title, artist, year}: the cover is fetched
        here, by the source's own id) or by hand ({title, artist, year})."""
        library = self.library()
        name, ref = str(body.get("source") or ""), str(body.get("ref") or "")
        cover, warning = None, ""
        if name:
            src = self.source(name)
            if library.find(src.name, ref):
                raise ValueError("This album is already in the library.")
            try:
                cover = src.cover(ref)
            except covers.CoverError as exc:
                warning = f"Added without a cover: {exc}"  # a photo can be added later
        record = library.add(body.get("title"), body.get("artist"), body.get("year"),
                             source=name, source_ref=ref if name else "", cover=cover)
        log.info("records: added %s / %s (%s)", record.artist, record.title, name or "by hand")
        return dict(record.to_json(), warning=warning)

    def update_record(self, record_id: str, body: dict) -> dict:
        record = self.library().update(record_id, body.get("title"), body.get("artist"),
                                       body.get("year"))
        self._refresh_now_playing(record)
        return record.to_json()

    def set_record_cover(self, record_id: str, data: bytes) -> dict:
        """A photo of the sleeve from the web UI (resized there: JPEG, at most 1200 px)."""
        record = self.library().set_cover(record_id, data)
        self._refresh_now_playing(record)
        return record.to_json()

    def delete_record(self, record_id: str) -> None:
        library = self.library()
        record = library.get(record_id)
        if self.now_playing and self.now_playing.id == record.id:
            self.set_now_playing(None)
        library.delete(record.id)

    def cover_file(self, req: Request) -> Response:
        resp = StaticFiles(self.library().covers).response("/" + req.params["name"],
                                                           req.headers)
        if resp is None:
            raise HTTPError(404, "no such cover")
        return resp

    # -- "now playing": the chosen record goes to the speakers and the Apple TV
    def set_now_playing(self, record_id: Any) -> dict:
        record = self.library().get(record_id) if record_id is not None else None
        if record is None and self.now_playing is None:
            return {"now_playing": None}
        self.now_playing = record
        try:
            self.settings.set("now_playing_record", record.id if record else None)
        except ValueError as exc:  # no SETTINGS_FILE: works, but not across restarts
            log.debug("now playing not saved: %s", exc)
        self._apply_now_playing(send=True)
        return {"now_playing": record.to_json() if record else None}

    def _refresh_now_playing(self, record: Record) -> None:
        """An edit of the record that is playing: the speakers show it right away."""
        if self.now_playing and self.now_playing.id == record.id:
            self.now_playing = record
            self._apply_now_playing(send=True)

    def _restore_now_playing(self) -> None:
        """After a restart (an update re-executes wormhole): the record chosen before."""
        saved = self.settings.get("now_playing_record")
        if saved is None or self.records is None:
            return
        try:
            self.now_playing = self.records.get(saved)
        except (ValueError, OSError):
            return
        self._apply_now_playing(send=False)

    def _apply_now_playing(self, send: bool) -> None:
        title, artist, artwork = self.default_now_playing
        record = self.now_playing
        if record is not None and self.records is not None:
            title, artist = record.title, record.artist
            artwork = self.records.cover_path(record) or artwork
        # Used when playback (re)starts: OwnTone.play_pipe puts it on the queue item.
        self.owntone.title, self.owntone.artist, self.owntone.artwork = title, artist, artwork
        if not send or self.meta_pipe is None:
            return
        # While the pipe plays: straight to the speakers through the metadata pipe.
        try:
            picture = artwork.read_bytes() if artwork is not None else None
            if not self.meta_pipe.send(title, artist, picture):
                log.debug("now playing: OwnTone isn't playing the pipe, used on next start")
        except (OSError, ValueError) as exc:
            log.warning("now playing: could not update OwnTone: %s", exc)

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


def routes(app: App) -> Router:
    """Every endpoint the frontend uses (src/wormhole/frontend/js/)."""
    r = Router()
    r.add("GET", "/api/state", lambda req: app.state())
    r.add("PUT", "/api/outputs/{id}",
          lambda req: app.update_output(req.params["id"], req.json()))
    r.add("PUT", "/api/outputs/{id}/hidden",
          lambda req: app.set_hidden(req.params["id"], req.json().get("hidden")))
    r.add("POST", "/api/outputs/{id}/pair",
          lambda req: app.pair_output(req.params["id"], str(req.json().get("pin", ""))))
    r.add("PUT", "/api/player/volume", lambda req: app.player_volume(req.json()))
    r.add("POST", "/api/player/play", lambda req: app.player_play())
    r.add("POST", "/api/player/pause", lambda req: app.player_pause())
    r.add("POST", "/api/player/stop", lambda req: app.player_stop())
    r.add("PUT", "/api/renderer", lambda req: app.set_renderer_name(req.json().get("name")))
    r.add("GET", "/api/records", lambda req: app.list_records())
    r.add("GET", "/api/records/search", lambda req: app.search_records(req.query))
    r.add("POST", "/api/records", lambda req: app.add_record(req.json()))
    r.add("PUT", "/api/records/{id}", lambda req: app.update_record(req.params["id"], req.json()))
    r.add("PUT", "/api/records/{id}/cover",
          lambda req: app.set_record_cover(req.params["id"], req.body))
    r.add("DELETE", "/api/records/{id}", lambda req: app.delete_record(req.params["id"]))
    r.add("PUT", "/api/now-playing", lambda req: app.set_now_playing(req.json().get("record")))
    r.add("GET", "/covers/{name}", app.cover_file)
    r.add("GET", "/api/update", lambda req: app.update_status())
    r.add("POST", "/api/update/check", lambda req: dict(app.check_update(), ok=True))
    r.add("POST", "/api/update/apply", lambda req: dict(app.apply_update(), ok=True))
    r.add("POST", "/api/update/rollback",
          lambda req: dict(app.apply_update(rollback=True), ok=True))
    return r


def make_handler(app: App, frontend: Path = FRONTEND) -> type:
    """Request handler class: the API plus the static frontend."""
    errors = {
        PinRequired: lambda exc: json_response(
            {"ok": False, "needs_pin": True, "error": str(exc)}, 400),
        OwnToneError: lambda exc: json_response({"ok": False, "error": str(exc)}, 400),
        ValueError: lambda exc: json_response({"ok": False, "error": str(exc)}, 400),
    }
    return HTTPApp(routes(app), StaticFiles(frontend), errors).handler_class()


def _raise_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def extra_port(cfg: Dict[str, str]) -> Optional[int]:
    """WEB_EXTRA_PORT: a second port for the same UI (80 = no port in the URL)."""
    value = cfg.get("WEB_EXTRA_PORT", "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise SystemExit(f"WEB_EXTRA_PORT must be a port number, not {value!r}")
    return int(value)


def listen_extra(port: Optional[int], handler) -> Optional[QuietHTTPServer]:
    """Serve the UI on the extra port too. Not fatal when it fails (port 80 needs
    CAP_NET_BIND_SERVICE, granted by server/wormhole.service): WEB_PORT still works."""
    if port is None:
        return None
    try:
        server = QuietHTTPServer(("", port), handler)
    except OSError as exc:
        log.warning("web UI not on extra port %d (WEB_EXTRA_PORT): %s", port, exc)
        return None
    threading.Thread(target=server.serve_forever, name="web-extra", daemon=True).start()
    log.info("web UI also on port %d", server.server_port)
    return server


def serve(cfg: Dict[str, str], port: int) -> int:
    app = App(cfg)
    handler = make_handler(app)
    try:
        server = QuietHTTPServer(("", port), handler)
    except OSError as exc:
        raise SystemExit(f"cannot listen on port {port}: {exc}") from None
    extra = listen_extra(extra_port(cfg), handler)
    # SIGTERM (systemctl stop on the server) gets the same clean shutdown as Ctrl+C.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    if app.meta_pipe is not None:
        try:
            app.meta_pipe.ensure()  # before any playback starts: OwnTone looks for it then
        except OSError as exc:
            log.warning("no mid-stream now playing updates: %s", exc)
    if app.renderer_name():
        try:
            app.start_renderer(app.renderer_name())
        except OSError as exc:
            raise SystemExit(f"cannot start UPnP renderer: {exc}") from None
    restart = threading.Event()

    def request_restart() -> None:
        restart.set()
        # shutdown() blocks until serve_forever returns, so not from a request thread directly;
        # the short delay lets the HTTP response reach the browser first.
        threading.Timer(0.5, server.shutdown).start()

    app.request_restart = request_restart
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
        if extra is not None:
            extra.shutdown()
            extra.server_close()
    if restart.is_set():
        _reexec()
    return 0


def _reexec() -> None:
    """Replace this process with a fresh `wormhole web` that loads the updated code.
    Same PID, so systemd (server) and the terminal (Mac) keep tracking it. Sockets are not
    inherited (Python makes them non-inheritable), so the ports are free again."""
    argv = [sys.executable, "-m", "wormhole"] + sys.argv[1:]
    log.info("restarting: %s", " ".join(argv))
    logging.shutdown()
    os.execv(sys.executable, argv)
