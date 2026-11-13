"""OwnTone client against an in-process fake of the JSON API (loopback only)."""

import json
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


class FakeOwnTone:
    def __init__(self) -> None:
        self.requests: List[Tuple[str, str, object]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append(("GET", self.path, None))
                if self.path == "/api/outputs":
                    return self._reply(200, OUTPUTS)
                if self.path == "/api/player":
                    return self._reply(200, {"state": "play", "volume": 40})
                self._reply(404, None)

            def do_PUT(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append(("PUT", self.path, body))
                self._reply(204 if self.path != "/api/outputs/999" else 400, None)

            def _reply(self, status, data):
                raw = json.dumps(data).encode() if data is not None else b""
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
        self.assertEqual(self.fake.requests[-1][:2], ("PUT", "/api/player/stop"))

    def test_http_error_raised(self):
        with self.assertRaisesRegex(OwnToneError, "HTTP 400"):
            self.ot.update_output("999", selected=True)

    def test_unreachable_server(self):
        with self.assertRaisesRegex(OwnToneError, "cannot reach OwnTone"):
            OwnTone("http://127.0.0.1:1", timeout=1).outputs()


if __name__ == "__main__":
    unittest.main()
