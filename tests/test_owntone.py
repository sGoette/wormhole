"""OwnTone client against an in-process fake of the JSON API (loopback only)."""

import json
from pathlib import Path
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Tuple

from wormhole.owntone import OwnTone, OwnToneError

OUTPUTS = {"outputs": [
    {"id": "111", "name": "Kitchen HomePod", "type": "AirPlay", "selected": False,
     "volume": 20, "requires_auth": False, "needs_auth_key": False},
    {"id": "222", "name": "Bedroom HomePod", "type": "AirPlay", "selected": True,
     "volume": 35, "requires_auth": False, "needs_auth_key": True},
    {"id": "333", "name": "Bedroom Lamp", "type": "AirPlay", "selected": False,
     "volume": 0, "requires_auth": False, "needs_auth_key": False},
]}


BAD_REQUEST = (b"<html> <head> <title>400 Bad Request</title> </head> <body> "
               b"<h1>Bad Request</h1> </body> </html>")  # what OwnTone 29.3 sends on failure

# Not in OUTPUTS (keeps the listing tests stable); behaves like an Apple TV that needs PIN pairing:
# switching on fails until paired, the PIN is "1234".
PIN_DEVICE = {"id": "444", "name": "Apple TV", "type": "AirPlay 2", "selected": False,
              "volume": 45, "requires_auth": True, "needs_auth_key": True}


class FakeOwnTone:
    def __init__(self) -> None:
        self.requests: List[Tuple[str, str, object]] = []
        self.paired = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append(("GET", self.path, None))
                if self.path == "/api/outputs":
                    return self._reply(200, OUTPUTS)
                if self.path == "/api/outputs/444":
                    return self._reply(200, dict(PIN_DEVICE, needs_auth_key=not outer.paired))
                for out in OUTPUTS["outputs"]:
                    if self.path == f"/api/outputs/{out['id']}":
                        return self._reply(200, out)
                if self.path == "/api/player":
                    return self._reply(200, {"state": "play", "volume": 40})
                if self.path.startswith("/api/search?type=tracks&expression=data_kind%20is"):
                    return self._reply(200, {"tracks": {"items": [
                        {"id": 7, "uri": "library:track:7", "path": "/srv/lib/turntable"}]}})
                self._reply(404, None)

            def do_POST(self):
                outer.requests.append(("POST", self.path, None))
                self._reply(200, {"count": 1, "items": [{"id": 42}]})

            def do_PUT(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append(("PUT", self.path, body))
                if self.path == "/api/outputs/999":
                    return self._reply(400, None, BAD_REQUEST)
                if self.path == "/api/outputs/444":
                    if "pin" in body:
                        outer.paired = body["pin"] == "1234"
                        return self._reply(204 if outer.paired else 400, None, BAD_REQUEST)
                    if body.get("selected") and not outer.paired:
                        return self._reply(400, None, BAD_REQUEST)  # device now shows a PIN
                self._reply(204, None)

            def _reply(self, status, data, raw=b""):
                raw = json.dumps(data).encode() if data is not None else raw
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class OwnToneTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOwnTone()
        self.addCleanup(self.fake.close)
        self.ot = OwnTone(self.fake.url)

    def test_outputs_parsed(self):
        outs = self.ot.outputs()
        self.assertEqual([o.name for o in outs][:2], ["Kitchen HomePod", "Bedroom HomePod"])
        self.assertTrue(outs[1].needs_auth_key)

    def test_find_outputs_by_unique_part(self):
        self.assertEqual([o.id for o in self.ot.find_outputs(["kitchen", "bedroom homepod"])],
                         ["111", "222"])

    def test_find_outputs_ambiguous_and_missing(self):
        with self.assertRaisesRegex(OwnToneError, "ambiguous 'Bedroom'"):
            self.ot.find_outputs(["Bedroom"])
        with self.assertRaisesRegex(OwnToneError, "no output matches 'Garage'"):
            self.ot.find_outputs(["Garage"])

    def test_set_outputs_and_update(self):
        self.ot.set_outputs(["111", "222"])
        self.ot.update_output("111", volume=30, pin="1234")
        self.assertEqual(self.fake.requests[-2], ("PUT", "/api/outputs/set",
                                                  {"outputs": ["111", "222"]}))
        self.assertEqual(self.fake.requests[-1], ("PUT", "/api/outputs/111",
                                                  {"volume": 30, "pin": "1234"}))

    def test_player_controls(self):
        self.assertEqual(self.ot.player()["state"], "play")
        self.ot.stop()
        # stop must also clear the queue, or pipe autostart never fires again
        self.assertEqual([r[:2] for r in self.fake.requests[-2:]],
                         [("PUT", "/api/player/stop"), ("PUT", "/api/queue/clear")])

    def test_play_pipe_queues_and_starts_it(self):
        self.ot.play_pipe("turntable")
        self.assertEqual(self.fake.requests[-1][:2], (
            "POST", "/api/queue/items/add?uris=library:track:7&clear=true&playback=start"))

    def test_play_pipe_sets_artwork_before_starting(self):
        # OwnTone sends artwork only when playback starts, so: add, set artwork, then play
        self.ot.artwork = Path("/srv/wormhole/now playing.jpg")
        self.ot.play_pipe("turntable")
        self.assertEqual([r[:2] for r in self.fake.requests[-3:]], [
            ("POST", "/api/queue/items/add?uris=library:track:7&clear=true"),
            ("PUT", "/api/queue/items/42?artwork_url=file:/srv/wormhole/now%20playing.jpg"),
            ("PUT", "/api/player/play")])

    def test_play_pipe_plays_even_if_artwork_fails(self):
        self.ot.artwork = Path("/srv/art.jpg")

        def fail(item_id, image):
            raise OwnToneError("PUT /api/queue/items/42: HTTP 400")
        self.ot.set_artwork = fail
        with self.assertLogs("wormhole.owntone", "WARNING"):
            self.ot.play_pipe("turntable")
        self.assertEqual(self.fake.requests[-1][:2], ("PUT", "/api/player/play"))

    def test_play_pipe_unknown_name(self):
        with self.assertRaisesRegex(OwnToneError, "no unique pipe named 'other'.*turntable"):
            self.ot.play_pipe("other")

    def test_http_error_html_reduced_to_title(self):
        with self.assertRaises(OwnToneError) as ctx:
            self.ot.update_output("999", selected=True)
        self.assertEqual(str(ctx.exception), "PUT /api/outputs/999: HTTP 400 Bad Request")

    def test_unreachable_server(self):
        with self.assertRaisesRegex(OwnToneError, "cannot reach OwnTone"):
            OwnTone("http://127.0.0.1:1", timeout=1).outputs()


REAL_LOG = """\
[2026-11-13 11:19:55] [ INFO] [   worker (5392)]  airplay: Failed to retrieve artwork
[2026-11-13 11:21:11] [  LOG] [   player (9792)]  airplay: Response to GET /info from \
'Kitchen HomePod' was negative, aborting (403 Forbidden)
[2026-11-13 11:21:11] [  LOG] [   player (9792)]   player: The AirPlay 2 device \
'Kitchen HomePod' failed to activate
[2026-11-13 11:21:11] [  LOG] [   worker (2272)]      web: JSON api request failed with \
error code 400 (/api/outputs/111)
"""


class LogTest(unittest.TestCase):
    def test_problems_since_filters_time_level_and_noise(self):
        import tempfile
        import time
        from pathlib import Path
        from wormhole.owntone import log_problems_since
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "owntone.log"
            log.write_text(REAL_LOG)
            since = time.mktime(time.strptime("2026-11-13 11:21:11", "%Y-%m-%d %H:%M:%S"))
            self.assertEqual(log_problems_since(log, since), [
                "airplay: Response to GET /info from 'Kitchen HomePod' was negative, "
                "aborting (403 Forbidden)",
                "player: The AirPlay 2 device 'Kitchen HomePod' failed to activate",
            ])
            self.assertEqual(log_problems_since(log, since + 60), [])
            self.assertEqual(log_problems_since(Path(d) / "missing.log", 0), [])


if __name__ == "__main__":
    unittest.main()
