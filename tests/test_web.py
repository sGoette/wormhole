"""Web UI API against the fake OwnTone from test_owntone (loopback only, no ffmpeg)."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from test_owntone import FakeOwnTone
from wormhole import web


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


class WebTest(unittest.TestCase):
    def setUp(self):
        FakeFeeder.instances = []
        self.fake = FakeOwnTone()
        self.addCleanup(self.fake.close)
        cfg = {"OWNTONE_URL": self.fake.url, "PCM_PIPE": "/tmp/turntable",
               "PIPE_SAMPLE_RATE": "48000", "PIPE_BITS": "16", "FEED_INPUT": "tone",
               "FEED_PRESETS": "http://mac:8765/live/tone/stream.mp3, "}
        self.app = web.App(cfg, web.FeedControl(cfg, factory=FakeFeeder))
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

    def test_page_sends_pin_only_via_pair(self):
        # Regression: a leftover "Send PIN" handler overwrote the Pair handler and still
        # sent PUT {pin}, which the server refuses.
        pin_calls = [line for line in web.PAGE.splitlines() if "{pin:" in line]
        self.assertEqual(len(pin_calls), 1, pin_calls)
        self.assertIn("/pair`", pin_calls[0])
        self.assertEqual(web.PAGE.count(".onclick ="), web.PAGE.count("pairBtn.onclick") + 3)

    def test_page_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=3) as resp:
            self.assertIn(b"<title>Wormhole</title>", resp.read())

    def test_state_lists_speakers_sorted_and_presets(self):
        status, s = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual([o["name"] for o in s["outputs"]],
                         ["Bedroom HomePod", "Bedroom Lamp", "Kitchen HomePod"])
        self.assertEqual(s["player"]["state"], "play")
        self.assertEqual(s["presets"][:2], ["tone", "http://mac:8765/live/tone/stream.mp3"])
        self.assertFalse(s["feed"]["running"])

    def test_toggle_and_volume_proxied_to_owntone(self):
        self.assertEqual(self.call("PUT", "/api/outputs/111", {"selected": True})[0], 200)
        self.assertEqual(self.call("PUT", "/api/outputs/111", {"volume": 42})[0], 200)
        self.assertEqual(self.fake.requests[-2], ("PUT", "/api/outputs/111", {"selected": True}))
        self.assertEqual(self.fake.requests[-1], ("PUT", "/api/outputs/111", {"volume": 42}))

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

    def test_feed_start_and_stop(self):
        self.assertEqual(self.call("POST", "/api/feed/start", {"source": "tone"})[0], 200)
        feeder = FakeFeeder.instances[-1]
        self.assertEqual((feeder.source, feeder.rate, feeder.pipe),
                         ("tone", 48000, Path("/tmp/turntable")))
        self.assertTrue(feeder.first_data_handled.wait(2))
        self.assertEqual(self.fake.requests[-1][:2], (
            "POST", "/api/queue/items/add?uris=library:track:7&clear=true&playback=start"))
        self.assertTrue(self.call("GET", "/api/state")[1]["feed"]["running"])
        self.assertEqual(self.call("POST", "/api/feed/stop")[0], 200)
        self.assertTrue(feeder.stopping)
        self.assertFalse(self.call("GET", "/api/state")[1]["feed"]["running"])

    def test_restarting_feed_stops_previous(self):
        self.call("POST", "/api/feed/start", {"source": "tone"})
        self.call("POST", "/api/feed/start", {"source": "http://x/stream.mp3"})
        first, second = FakeFeeder.instances
        self.assertTrue(first.stopping)
        self.assertFalse(second.stopping)

    def test_feed_rejects_unknown_source(self):
        status, body = self.call("POST", "/api/feed/start", {"source": "/no/such/file.mp3"})
        self.assertEqual(status, 400)
        self.assertIn("existing file", body["error"])
        self.assertEqual(FakeFeeder.instances, [])

    def test_stop_everything(self):
        self.call("POST", "/api/feed/start", {"source": "tone"})
        self.assertEqual(self.call("POST", "/api/stop")[0], 200)
        self.assertTrue(FakeFeeder.instances[-1].stopping)
        self.assertEqual([r[:2] for r in self.fake.requests[-2:]],
                         [("PUT", "/api/player/stop"), ("PUT", "/api/queue/clear")])

    def test_owntone_down_still_serves_state(self):
        self.fake.close()
        status, s = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("cannot reach OwnTone", s["error"])
        self.assertEqual(s["outputs"], [])


if __name__ == "__main__":
    unittest.main()
