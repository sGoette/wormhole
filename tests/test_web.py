"""Web UI API against the fake OwnTone from test_owntone (loopback only, no ffmpeg)."""

import json
import re
import struct
import tempfile
import types
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_owntone import FakeOwnTone
from wormhole import config, feed, web
from wormhole.config import ROOT

JS = web.FRONTEND / "js"


class FakeFeeder:
    """Stands in for feed.Feeder: records its arguments, 'receives data' at once (calls
    on_first_data like the real one), runs until stopped."""

    instances = []

    def __init__(self, source, pipe, rate, bits, on_first_data=None):
        self.source, self.pipe, self.rate, self.bits = source, pipe, rate, bits
        self.on_first_data = on_first_data
        self.stopping = False
        self.started_at = 0.0
        self.restarts, self.last_error = 0, ""
        self.bytes_written = 0
        self.input = None
        self.first_data_handled = threading.Event()
        self._done = threading.Event()
        FakeFeeder.instances.append(self)

    def run(self):
        self.started_at = 1.0
        if self.on_first_data is not None:
            try:
                self.on_first_data()
            except Exception as exc:  # noqa: BLE001 - like feed.Feeder
                self.last_error = f"could not start playback: {exc}"
        self.first_data_handled.set()
        self._done.wait(5)
        return 0

    def flowing(self):
        return not self.stopping

    def stop(self):
        self.stopping = True
        self._done.set()


class FakeRendererService:
    """Stands in for renderer.RendererService (no sockets, no SSDP)."""

    instances = []

    def __init__(self, name, port, feed_control, owntone, host_ip=None, pipe_name=""):
        self.renderer = types.SimpleNamespace(
            name=name, status=lambda: {"name": self.renderer.name, "state": "STOPPED",
                                       "uri": "", "controller": ""})
        self.port, self.started, self.renames = port, False, []
        FakeRendererService.instances.append(self)

    def start(self):
        self.started = True

    def rename(self, name):
        self.renames.append(name)
        self.renderer.name = name


class WebTest(unittest.TestCase):
    def setUp(self):
        FakeFeeder.instances = []
        self.fake = FakeOwnTone()
        self.addCleanup(self.fake.close)
        cfg = {"OWNTONE_URL": self.fake.url, "PCM_PIPE": "/tmp/turntable",
               "PIPE_SAMPLE_RATE": "48000", "PIPE_BITS": "16", "RENDERER_PORT": "0"}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_file = Path(tmp.name) / "state" / "settings.json"
        cfg["SETTINGS_FILE"] = str(self.settings_file)
        FakeRendererService.instances = []
        self.app = web.App(cfg, feed.FeedControl(cfg, factory=FakeFeeder),
                           renderer_factory=FakeRendererService)
        self.addCleanup(self.app.feed.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_frontend_sends_pin_only_via_pair(self):
        # Regression: a leftover "Send PIN" handler overwrote the Pair handler and still
        # sent PUT {pin}, which the server refuses.
        code = (JS / "speakers.js").read_text()
        pin_calls = [line for line in code.splitlines() if "{pin:" in line]
        self.assertEqual(len(pin_calls), 1, pin_calls)
        self.assertIn("/pair`", pin_calls[0])
        self.assertEqual(code.count("pinForm.onsubmit"), 1)

    def test_every_api_path_in_the_frontend_has_a_route(self):
        router = web.routes(self.app)
        paths = set()
        for path in JS.glob("*.js"):
            paths |= set(re.findall(r'[`"](/api/[^`"]*)[`"]', path.read_text()))
        self.assertGreaterEqual(len(paths), 6)
        for path in paths:
            # `/api/update/${kind}` is apply or rollback; other placeholders are ids.
            path = path.replace("${kind}", "apply")
            path = re.sub(r"\$\{[^}]+\}", "x", path)
            self.assertTrue(router.allowed(path), path)

    def test_page_and_assets_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=3) as resp:
            self.assertIn(b"<title>Wormhole</title>", resp.read())
        for path in ("/css/style.css", "/js/main.js", "/js/api.js"):
            with urllib.request.urlopen(self.base + path, timeout=3) as resp:
                self.assertEqual(resp.status, 200)

    def test_unknown_api_path_is_json_404(self):
        self.assertEqual(self.call("GET", "/api/nope"), (404, {"ok": False,
                                                              "error": "not found: /api/nope"}))

    def test_state_lists_speakers_sorted(self):
        status, s = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual([o["name"] for o in s["outputs"]],
                         ["Bedroom HomePod", "Bedroom Lamp", "Kitchen HomePod"])
        self.assertEqual(s["player"]["state"], "play")
        self.assertIsNone(s["renderer"])

    def test_toggle_and_volume_proxied_to_owntone(self):
        self.assertEqual(self.call("PUT", "/api/outputs/111", {"selected": True})[0], 200)
        self.assertEqual(self.call("PUT", "/api/outputs/111", {"volume": 42})[0], 200)
        self.assertEqual(self.fake.requests[-2], ("PUT", "/api/outputs/111", {"selected": True}))
        self.assertEqual(self.fake.requests[-1], ("PUT", "/api/outputs/111", {"volume": 42}))

    def test_player_bar_proxied_to_owntone(self):
        self.assertEqual(self.call("PUT", "/api/player/volume", {"volume": 30})[0], 200)
        self.assertEqual(self.fake.requests[-1][:2], ("PUT", "/api/player/volume?volume=30"))
        self.assertEqual(self.call("POST", "/api/player/pause")[0], 200)
        self.assertEqual(self.fake.requests[-1][:2], ("PUT", "/api/player/pause"))
        self.assertEqual(self.call("POST", "/api/player/play")[0], 200)  # no turntable stream
        self.assertEqual(self.fake.requests[-1][:2], ("PUT", "/api/player/play"))
        for bad in (150, -1, "50", True, None):
            self.assertEqual(self.call("PUT", "/api/player/volume", {"volume": bad})[0], 400)

    def test_play_reconnects_the_turntable_stream(self):
        restarted = []
        self.app.renderer = types.SimpleNamespace(renderer=types.SimpleNamespace(
            restart_stream=lambda: restarted.append(1) or True))
        before = len(self.fake.requests)
        self.assertEqual(self.call("POST", "/api/player/play")[0], 200)
        self.assertEqual(restarted, [1])
        self.assertEqual(len(self.fake.requests), before)  # no OwnTone play: the feed does it

    def test_state_source_is_the_now_playing_title(self):
        self.app.cfg["NOW_PLAYING_TITLE"] = "Turntable"
        self.app.renderer = types.SimpleNamespace(renderer=types.SimpleNamespace(
            status=lambda: {"name": "Wormhole", "state": "PLAYING"}))
        state = self.call("GET", "/api/state")[1]
        self.assertEqual(state["renderer"]["source"], "Turntable")
        self.assertIsNone(state["input"])  # nothing decoded yet

    def test_invalid_volume_rejected_before_owntone(self):
        status, body = self.call("PUT", "/api/outputs/111", {"volume": 150})
        self.assertEqual(status, 400)
        self.assertIn("0-100", body["error"])
        self.assertFalse([r for r in self.fake.requests if r[0] == "PUT"])

    def test_owntone_error_reported(self):
        status, body = self.call("PUT", "/api/outputs/999", {"selected": True})
        self.assertEqual(status, 400)
        self.assertIn("HTTP 400 Bad Request", body["error"])

    def test_switching_on_a_pin_device_asks_for_the_pin(self):
        status, body = self.call("PUT", "/api/outputs/444", {"selected": True})
        self.assertEqual(status, 400)
        self.assertTrue(body["needs_pin"])
        self.assertIn("enter it and press Pair", body["error"])

    def test_pair_sends_pin_alone_then_switches_on(self):
        self.call("PUT", "/api/outputs/444", {"selected": True})  # device shows a PIN
        status, body = self.call("POST", "/api/outputs/444/pair", {"pin": " 1234 "})
        self.assertEqual((status, body), (200, {"ok": True}))
        puts = [r[2] for r in self.fake.requests if r[:2] == ("PUT", "/api/outputs/444")]
        self.assertEqual(puts[-2:], [{"pin": "1234"}, {"selected": True}])  # order per #1967

    def test_pair_with_wrong_pin(self):
        status, body = self.call("POST", "/api/outputs/444/pair", {"pin": "9999"})
        self.assertEqual(status, 400)
        self.assertIn("Pairing failed", body["error"])

    def test_empty_pin_never_reaches_owntone(self):
        status, body = self.call("POST", "/api/outputs/444/pair", {"pin": "  "})
        self.assertEqual(status, 400)
        self.assertIn("Enter the PIN", body["error"])
        self.assertFalse([r for r in self.fake.requests if r[0] == "PUT"])

    def test_pin_via_put_is_refused(self):
        status, body = self.call("PUT", "/api/outputs/444", {"pin": "1234"})
        self.assertEqual(status, 400)
        self.assertIn("/pair", body["error"])

    def test_owntone_error_explained_from_log(self):
        import tempfile
        import time
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{stamp}] [  LOG] [   player (9792)]  airplay: Response to GET /info "
                    "from 'Kitchen HomePod' was negative, aborting (403 Forbidden)\n")
        self.addCleanup(Path(f.name).unlink)
        self.app.owntone_log = Path(f.name)
        status, body = self.call("PUT", "/api/outputs/999", {"selected": True})
        self.assertEqual(status, 400)
        self.assertIn("OwnTone log: airplay: Response to GET /info", body["error"])
        self.assertIn('Hint: HomePod software 27 rejects', body["error"])
        self.assertIn('user_agent = "AirPlay/999.0.0"', body["error"])

    def test_renderer_name_starts_renderer_and_is_saved(self):
        status, body = self.call("PUT", "/api/renderer", {"name": "  Living   Room "})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["name"], "Living Room")
        self.assertTrue(FakeRendererService.instances[-1].started)
        self.assertEqual(json.loads(self.settings_file.read_text()),
                         {"renderer_name": "Living Room"})
        self.assertEqual(self.call("GET", "/api/state")[1]["renderer"]["name"], "Living Room")

    def test_renaming_a_running_renderer(self):
        self.app.start_renderer("Wormhole")
        service = FakeRendererService.instances[-1]
        self.assertEqual(self.call("PUT", "/api/renderer", {"name": "Kitchen"})[0], 200)
        self.assertEqual(service.renames, ["Kitchen"])
        self.call("PUT", "/api/renderer", {"name": "Kitchen"})  # unchanged: no re-announce
        self.assertEqual(service.renames, ["Kitchen"])
        self.assertEqual(len(FakeRendererService.instances), 1)

    def test_saved_name_wins_over_env(self):
        self.app.cfg["RENDERER_NAME"] = "From env"
        self.assertEqual(self.app.renderer_name(), "From env")
        self.call("PUT", "/api/renderer", {"name": "From UI"})
        reloaded = web.App(self.app.cfg, renderer_factory=FakeRendererService)
        self.assertEqual(reloaded.renderer_name(), "From UI")

    def test_invalid_renderer_names(self):
        for name in ["", "   ", "x" * 65, "bell\x07", None, 5]:
            status, body = self.call("PUT", "/api/renderer", {"name": name})
            self.assertEqual(status, 400, name)
        self.assertFalse(self.settings_file.exists())
        self.assertEqual(FakeRendererService.instances, [])

    def test_renaming_without_settings_file_is_refused(self):
        self.app.settings.path = None
        status, body = self.call("PUT", "/api/renderer", {"name": "Kitchen"})
        self.assertEqual(status, 400)
        self.assertIn("SETTINGS_FILE", body["error"])

    def test_hide_and_show_a_speaker(self):
        status, body = self.call("PUT", "/api/outputs/111/hidden", {"hidden": True})
        self.assertEqual((status, body), (200, {"ok": True}))
        self.call("PUT", "/api/outputs/333/hidden", {"hidden": True})
        state = {o["name"]: o["hidden"] for o in self.call("GET", "/api/state")[1]["outputs"]}
        self.assertEqual(state, {"Bedroom HomePod": False, "Bedroom Lamp": True,
                                 "Kitchen HomePod": True})
        self.assertEqual(json.loads(self.settings_file.read_text())["hidden_outputs"],
                         ["Bedroom Lamp", "Kitchen HomePod"])
        self.call("PUT", "/api/outputs/111/hidden", {"hidden": False})
        self.assertEqual(self.app.hidden_outputs(), ["Bedroom Lamp"])

    def test_hiding_a_playing_speaker_switches_it_off(self):
        self.call("PUT", "/api/outputs/222/hidden", {"hidden": True})  # 222 is selected
        self.assertIn(("PUT", "/api/outputs/222", {"selected": False}), self.fake.requests)
        self.fake.requests.clear()
        self.call("PUT", "/api/outputs/111/hidden", {"hidden": True})  # 111 is not
        self.assertFalse([r for r in self.fake.requests if r[0] == "PUT"])

    def test_hidden_needs_a_boolean(self):
        status, body = self.call("PUT", "/api/outputs/111/hidden", {"hidden": "yes"})
        self.assertEqual(status, 400)
        self.assertFalse(self.settings_file.exists())

    def test_broken_hidden_list_in_settings_is_ignored(self):
        self.app.settings.values["hidden_outputs"] = "Kitchen HomePod"
        self.assertEqual(self.app.hidden_outputs(), [])
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def test_feed_endpoints_are_gone(self):
        for path in ("/api/feed/start", "/api/feed/stop", "/api/stop"):
            self.assertEqual(self.call("POST", path, {})[0], 404, path)

    def test_manifest_and_icons_served(self):
        with urllib.request.urlopen(self.base + "/manifest.webmanifest", timeout=3) as resp:
            self.assertEqual(resp.headers["Content-Type"], "application/manifest+json")
            manifest = json.loads(resp.read())
        self.assertEqual(manifest["display"], "standalone")
        page = urllib.request.urlopen(self.base + "/", timeout=3).read().decode()
        icons = [i["src"] for i in manifest["icons"]]
        icons += re.findall(r'rel="(?:apple-touch-icon|icon)"[^>]*href="([^"]+)"', page)
        self.assertGreaterEqual(len(icons), 5)
        signatures = {".png": b"\x89PNG\r\n\x1a\n", ".ico": b"\x00\x00\x01\x00",
                      ".svg": b"<svg "}
        # header icon: dark and light version
        logos = re.findall(r'<(?:source srcset|img src)="(/icons/[^"]+\.svg)"', page)
        self.assertEqual(len(logos), 2)
        for src in icons + logos:
            sig = signatures[src[src.rindex("."):]]
            with urllib.request.urlopen(self.base + src, timeout=3) as resp:
                self.assertEqual(resp.read(len(sig)), sig, src)

    def test_now_playing_artwork_is_a_big_jpeg(self):
        art = config.path(config.load(str(ROOT / "config" / "local.env")),
                          "NOW_PLAYING_ARTWORK")
        data = art.read_bytes()
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        # JPEG SOF0 marker: height and width follow the precision byte
        sof = data.index(b"\xff\xc0")
        height, width = struct.unpack(">HH", data[sof + 5:sof + 9])
        self.assertEqual((width, height), (2160, 2160))

    def test_owntone_down_still_serves_state(self):
        self.fake.close()
        status, s = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("cannot reach OwnTone", s["error"])
        self.assertEqual(s["outputs"], [])


if __name__ == "__main__":
    unittest.main()


class ExtraPortTest(unittest.TestCase):
    """WEB_EXTRA_PORT: the same UI on a second port (80 on the server)."""

    def test_extra_port_setting(self):
        self.assertIsNone(web.extra_port({}))
        self.assertIsNone(web.extra_port({"WEB_EXTRA_PORT": ""}))
        self.assertEqual(web.extra_port({"WEB_EXTRA_PORT": "80"}), 80)
        with self.assertRaises(SystemExit):
            web.extra_port({"WEB_EXTRA_PORT": "eighty"})

    class Hello(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *args):
            pass

    def test_serves_the_given_handler(self):
        self.assertIsNone(web.listen_extra(None, self.Hello))
        server = web.listen_extra(0, self.Hello)  # 0 = any free port
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.read(), b"hello")

    def test_busy_port_is_not_fatal(self):
        busy = ThreadingHTTPServer(("", 0), self.Hello)
        self.addCleanup(busy.server_close)
        with self.assertLogs("wormhole.web", "WARNING"):
            self.assertIsNone(web.listen_extra(busy.server_port, self.Hello))
