"""Tests for tools/fake_turntable/. Loopback only: no LAN, no ffmpeg, no real renderer."""

import importlib.util
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Tuple

TOOL = Path(__file__).resolve().parents[1] / "tools" / "fake_turntable" / "fake_turntable.py"
spec = importlib.util.spec_from_file_location("fake_turntable", TOOL)
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)

AVT = "urn:schemas-upnp-org:service:AVTransport:1"

DESCRIPTION = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
 <device>
  <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
  <friendlyName>Kitchen &amp; Bar</friendlyName>
  <manufacturer>Acme</manufacturer><modelName>Box 1</modelName>
  <UDN>uuid:root</UDN>
  <serviceList>
   <service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
    <controlURL>/rc</controlURL></service>
  </serviceList>
  <deviceList><device><UDN>uuid:embedded</UDN><serviceList>
   <service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
    <controlURL>avt/control</controlURL></service>
  </serviceList></device></deviceList>
 </device>
</root>"""

HUE = b"""<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">
<URLBase>http://0.0.0.0:80/</URLBase><device><friendlyName>Hue Bridge</friendlyName>
<UDN>uuid:hue</UDN></device></root>"""

FAULT = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>
<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>
<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>701</errorCode>
</UPnPError></detail></s:Fault></s:Body></s:Envelope>"""


class FakeRenderer:
    """Serves a device description and answers AVTransport SOAP calls on 127.0.0.1."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, bytes]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._reply(200, DESCRIPTION)

            def do_POST(self):
                action = self.headers["SOAPACTION"].strip('"').split("#")[1]
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.calls.append((action, body))
                if action == "Pause":
                    return self._reply(500, FAULT)
                state = "<CurrentTransportState>PLAYING</CurrentTransportState>"
                out = state if action == "GetTransportInfo" else ""
                self._reply(200, (
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<u:{action}Response xmlns:u="{AVT}">{out}</u:{action}Response>'
                    "</s:Body></s:Envelope>").encode())

            def _reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/description.xml"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ParsingTest(unittest.TestCase):
    def test_fix_host_replaces_unspecified_address(self):
        self.assertEqual(ft.fix_host("http://0.0.0.0:80/d.xml", "10.0.0.5"),
                         "http://10.0.0.5:80/d.xml")
        self.assertEqual(ft.fix_host("http://1.2.3.4/d.xml", "10.0.0.5"), "http://1.2.3.4/d.xml")

    def test_ssdp_response_headers(self):
        raw = b"HTTP/1.1 200 OK\r\nLOCATION: http://x/d.xml\r\nST: a:b\r\n\r\n"
        h = ft.parse_ssdp_response(raw)
        self.assertEqual(h["location"], "http://x/d.xml")
        self.assertEqual(h["st"], "a:b")

    def test_finds_avtransport_in_embedded_device(self):
        r = ft.parse_description(DESCRIPTION, "http://10.0.0.5:1400/desc.xml", "10.0.0.5")
        self.assertEqual(r.name, "Kitchen & Bar")
        self.assertEqual(r.key, "uuid:root")
        self.assertEqual(r.avtransport_url, "http://10.0.0.5:1400/avt/control")
        self.assertEqual(r.avtransport_type, AVT)
        self.assertTrue(r.can_play)

    def test_non_renderer_cannot_play(self):
        r = ft.parse_description(HUE, "http://10.0.0.9/description.xml", "10.0.0.9")
        self.assertFalse(r.can_play)

    def test_fault_without_description_uses_spec_text(self):
        code, desc = ft.parse_fault(FAULT)
        self.assertEqual(code, 701)
        self.assertIn("Transition not available", desc)

    def test_didl_is_escaped_twice_in_envelope(self):
        didl = ft.didl_lite("A & B", "http://h/live/a%20b/stream.mp3", "audio/mpeg")
        self.assertIn("A &amp; B", didl)
        self.assertIn('protocolInfo="http-get:*:audio/mpeg:*"', didl)
        env = ft.soap_envelope(AVT, "SetAVTransportURI", [("CurrentURIMetaData", didl)])
        self.assertIn(b"&lt;DIDL-Lite", env)
        self.assertIn(b"A &amp;amp; B", env)


class FfmpegCommandTest(unittest.TestCase):
    def test_tone_mp3_is_live_and_headerless(self):
        cmd = ft.ffmpeg_command(None, "mp3", "320k")
        self.assertIn("-re", cmd)
        self.assertIn("lavfi", cmd)
        self.assertEqual(cmd[cmd.index("-write_xing") + 1], "0")
        self.assertEqual(cmd[-3:], ["-f", "mp3", "-"])

    def test_file_flac_loops(self):
        cmd = ft.ffmpeg_command(Path("/x/rec.flac"), "flac", "320k")
        self.assertEqual(cmd[cmd.index("-stream_loop") + 1], "-1")
        self.assertIn("/x/rec.flac", cmd)
        self.assertEqual(cmd[-3:], ["-f", "flac", "-"])

    def test_unknown_format_rejected(self):
        with self.assertRaises(ValueError):
            ft.ffmpeg_command(None, "ogg", "320k")


class SourcesTest(unittest.TestCase):
    def test_lists_tone_and_audio_files_only(self):
        with self._media("a.mp3", "b.FLAC", "notes.txt") as d:
            self.assertEqual(list(ft.Sources(d).all()), ["tone", "a.mp3", "b.FLAC"])

    def test_unknown_source_exits(self):
        with self._media() as d, self.assertRaises(SystemExit):
            ft.Sources(d).add("missing.mp3")

    def _media(self, *names):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        for n in names:
            (Path(tmp.name) / n).write_bytes(b"")
        return _Dir(tmp)


class _Dir:
    def __init__(self, tmp):
        self.tmp = tmp

    def __enter__(self) -> Path:
        return Path(self.tmp.name)

    def __exit__(self, *exc):
        self.tmp.cleanup()


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRenderer()
        self.addCleanup(self.fake.close)
        renderer = ft.describe(self.fake.url, "127.0.0.1")
        self.tt = ft.Turntable(ft.Sources(Path("/nonexistent")), 8765, "320k", use_didl=True)
        self.tt.renderers = {renderer.key: renderer}

    def actions(self):
        return [a for a, _ in self.fake.calls]

    def test_play_sends_uri_then_play(self):
        self.tt.play("Kitchen", "tone", "flac")
        self.assertEqual(self.actions(), ["SetAVTransportURI", "Play"])
        body = self.fake.calls[0][1]
        self.assertIn(b"http://127.0.0.1:8765/live/tone/stream.flac</CurrentURI>", body)
        self.assertIn(b"audio/flac", body)
        self.assertEqual(self.tt.snapshot()["transport"]["CurrentTransportState"], "PLAYING")

    def test_pause_error_is_reported_with_code(self):
        self.tt.play("Kitchen", "tone", "mp3")
        with self.assertRaises(ft.UPnPError) as ctx:
            self.tt.pause()
        self.assertEqual(ctx.exception.code, 701)
        self.assertEqual(self.tt.events[-1]["level"], "ERROR")

    def test_resume_after_pause_only_sends_play(self):
        self.tt.play("Kitchen", "tone", "mp3")
        self.tt.last_action = "pause"  # as if Pause had succeeded
        self.tt.play("Kitchen", "tone", "mp3")
        self.assertEqual(self.actions(), ["SetAVTransportURI", "Play", "Play"])

    def test_unknown_renderer_lists_candidates(self):
        with self.assertRaises(LookupError) as ctx:
            self.tt.find("Bedroom")
        self.assertIn("Kitchen & Bar (127.0.0.1)", str(ctx.exception))


class WebUITest(unittest.TestCase):
    def setUp(self):
        tt = ft.Turntable(ft.Sources(Path("/nonexistent")), 8765, "320k", use_didl=True)
        self.server = ft.make_server(tt, 0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=3) as resp:
                return resp.status, resp.headers["Content-Type"], resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers["Content-Type"], exc.read()

    def test_frontend_files_served(self):
        status, ctype, body = self.get("/")
        self.assertEqual((status, ctype), (200, "text/html; charset=utf-8"))
        self.assertIn(b"<title>Fake Turntable</title>", body)
        self.assertEqual(self.get("/app.js")[:2], (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.get("/style.css")[:2], (200, "text/css; charset=utf-8"))

    def test_nothing_outside_the_frontend(self):
        self.assertEqual(self.get("/../fake_turntable.py")[0], 404)
        self.assertEqual(self.get("/%2e%2e/fake_turntable.py")[0], 404)
        self.assertEqual(self.get("/missing.js")[0], 404)


if __name__ == "__main__":
    unittest.main()
