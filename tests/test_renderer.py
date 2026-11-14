"""UPnP renderer, driven by fake_turntable.py's own UPnP client (loopback, no SSDP, no ffmpeg)."""

import threading
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_fake_turntable import ft  # tools/fake_turntable/, loaded as a module
from test_owntone import FakeOwnTone
from test_web import FakeFeeder
from wormhole import renderer as rd
from wormhole.feed import FeedControl

URI = "http://192.0.2.10:8765/live/tone/stream.mp3"


class SpecTest(unittest.TestCase):
    def test_every_argument_has_a_state_variable(self):
        for service, (actions, variables) in rd.SPECS.items():
            for action, args in actions.items():
                for arg, direction, var in args:
                    self.assertIn(var, variables, f"{service}.{action}.{arg}")
                    self.assertIn(direction, ("in", "out"))

    def test_xml_is_well_formed_and_found_by_fake_turntable(self):
        for actions, variables in rd.SPECS.values():
            ET.fromstring(rd.scpd_xml(actions, variables))
        desc = rd.description_xml("Wormhole & Co", "uuid:abc")
        r = ft.parse_description(desc, "http://10.0.0.2:49494/description.xml", "10.0.0.2")
        self.assertEqual(r.name, "Wormhole & Co")
        self.assertTrue(r.can_play)
        self.assertEqual(r.avtransport_url, "http://10.0.0.2:49494/AVTransport/control")

    def test_every_action_has_a_handler(self):
        r = rd.Renderer("x", feed_control=None)
        for service, (actions, _) in rd.SPECS.items():
            for action in actions:
                self.assertTrue(hasattr(r, f"_{service}_{action}"), f"{service}.{action}")


class SSDPTest(unittest.TestCase):
    UDN = "uuid:1234"

    def test_matching_targets(self):
        self.assertEqual(len(rd.matching_targets("ssdp:all", self.UDN)), 6)
        self.assertEqual(rd.matching_targets(rd.DEVICE_TYPE, self.UDN), [rd.DEVICE_TYPE])
        self.assertEqual(rd.matching_targets(self.UDN, self.UDN), [self.UDN])
        self.assertEqual(rd.matching_targets("urn:schemas-upnp-org:device:Basic:1",
                                             self.UDN), [])

    def test_search_response_is_parsed_by_fake_turntable(self):
        raw = rd.search_response(rd.DEVICE_TYPE, self.UDN, "http://10.0.0.2:49494/d.xml")
        headers = ft.parse_ssdp_response(raw)
        self.assertEqual(headers["location"], "http://10.0.0.2:49494/d.xml")
        self.assertEqual(headers["usn"], f"{self.UDN}::{rd.DEVICE_TYPE}")
        self.assertEqual(headers["st"], rd.DEVICE_TYPE)

    def test_notify_messages(self):
        alive = rd.notify_message("upnp:rootdevice", self.UDN, "http://x/d.xml", alive=True)
        bye = rd.notify_message(self.UDN, self.UDN, "http://x/d.xml", alive=False)
        self.assertIn(b"NTS: ssdp:alive", alive)
        self.assertIn(b"LOCATION: http://x/d.xml", alive)
        self.assertIn(b"USN: uuid:1234\r\n", bye)
        self.assertNotIn(b"LOCATION", bye)

    def test_parse_msearch(self):
        start, h = rd.parse_request(b'M-SEARCH * HTTP/1.1\r\nMAN: "ssdp:discover"\r\n'
                                    b"ST: ssdp:all\r\nMX: 2\r\n\r\n")
        self.assertTrue(start.startswith("M-SEARCH"))
        self.assertEqual((h["MAN"], h["ST"]), ('"ssdp:discover"', "ssdp:all"))


class RendererTest(unittest.TestCase):
    def setUp(self):
        FakeFeeder.instances = []
        self.owntone_fake = FakeOwnTone()
        self.addCleanup(self.owntone_fake.close)
        cfg = {"PCM_PIPE": "/tmp/turntable"}
        self.feed = FeedControl(cfg, factory=FakeFeeder)
        self.addCleanup(self.feed.stop)
        self.service = rd.RendererService("Wormhole", 0, self.feed,
                                          rd.OwnTone(self.owntone_fake.url), ssdp=False)
        self.service.start()
        self.addCleanup(self.service.stop)
        url = f"http://127.0.0.1:{self.service.port}/description.xml"
        self.r = ft.describe(url, "127.0.0.1")  # the fake turntable's view of us

    def state(self):
        return ft.transport_info(self.r)["CurrentTransportState"]

    def test_fake_turntable_play_pause_stop(self):
        self.assertEqual(self.state(), "NO_MEDIA_PRESENT")
        ft.set_uri(self.r, URI, ft.didl_lite("tone", URI, "audio/mpeg"))
        self.assertEqual(self.state(), "STOPPED")
        ft.play(self.r)
        self.assertEqual(self.state(), "PLAYING")
        feeder = FakeFeeder.instances[-1]
        self.assertEqual(feeder.source, URI)
        self.assertEqual(self.feed.state()["origin"], "upnp")
        ft.pause(self.r)
        self.assertEqual(self.state(), "PAUSED_PLAYBACK")
        self.assertTrue(feeder.stopping)
        self.assertEqual([r[:2] for r in self.owntone_fake.requests[-2:]],
                         [("PUT", "/api/player/stop"), ("PUT", "/api/queue/clear")])
        ft.play(self.r)  # resume = reconnect to the live stream
        self.assertEqual(len(FakeFeeder.instances), 2)
        # Play after Stop must start the pipe explicitly (autostart does not fire again),
        # and only once the first audio is there
        self.assertTrue(FakeFeeder.instances[-1].first_data_handled.wait(2))
        self.assertEqual(self.owntone_fake.requests[-1][:2], (
            "POST", "/api/queue/items/add?uris=library:track:7&clear=true&playback=start"))
        ft.stop(self.r)
        self.assertEqual(self.state(), "STOPPED")
        self.assertTrue(FakeFeeder.instances[-1].stopping)

    def test_rename_is_live_and_a_new_device(self):
        old_udn = self.service.renderer.udn
        ft.set_uri(self.r, URI, "")
        ft.play(self.r)
        self.service.rename("Living Room Turntable")
        renamed = ft.describe(f"http://127.0.0.1:{self.service.port}/description.xml",
                              "127.0.0.1")
        self.assertEqual(renamed.name, "Living Room Turntable")
        self.assertNotEqual(renamed.key, old_udn)  # control points see a new device
        self.assertEqual(renamed.key, rd.udn_for("Living Room Turntable"))
        self.assertEqual(self.state(), "PLAYING")  # playback keeps running
        self.assertFalse(FakeFeeder.instances[-1].stopping)

    def test_udn_is_stable_per_name(self):
        self.assertEqual(rd.udn_for("A"), rd.udn_for("A"))
        self.assertNotEqual(rd.udn_for("A"), rd.udn_for("B"))

    def test_web_ui_taking_the_feed_stops_renderer(self):
        ft.set_uri(self.r, URI, "")
        ft.play(self.r)
        self.feed.start("tone", origin="web")
        self.assertEqual(self.state(), "STOPPED")
        ft.stop(self.r)  # must not stop the web UI's feed
        self.assertTrue(self.feed.owned_by("web"))

    def test_new_uri_while_playing_switches_stream(self):
        ft.set_uri(self.r, URI, "")
        ft.play(self.r)
        ft.set_uri(self.r, URI.replace("tone", "record.flac"), "")
        self.assertEqual(self.state(), "PLAYING")
        self.assertIn("record.flac", FakeFeeder.instances[-1].source)

    def test_faults(self):
        with self.assertRaises(ft.UPnPError) as ctx:
            ft.play(self.r)
        self.assertEqual(ctx.exception.code, 701)
        with self.assertRaises(ft.UPnPError) as ctx:
            ft.set_uri(self.r, "file:///etc/passwd", "")
        self.assertEqual(ctx.exception.code, 716)
        with self.assertRaises(ft.UPnPError) as ctx:
            ft.soap_call(self.r, "Play", [("InstanceID", "1"), ("Speed", "1")])
        self.assertEqual(ctx.exception.code, 718)
        with self.assertRaises(ft.UPnPError) as ctx:
            ft.soap_call(self.r, "Seek", [("InstanceID", "0"), ("Unit", "REL_TIME"),
                                          ("Target", "0:01:00")])
        self.assertEqual(ctx.exception.code, 710)

    def test_info_actions(self):
        ft.set_uri(self.r, URI, "")
        media = ft.soap_call(self.r, "GetMediaInfo", [("InstanceID", "0")])
        self.assertEqual((media["CurrentURI"], media["NrTracks"]), (URI, "1"))
        ft.play(self.r)
        pos = ft.soap_call(self.r, "GetPositionInfo", [("InstanceID", "0")])
        self.assertEqual(pos["TrackURI"], URI)
        actions = ft.soap_call(self.r, "GetCurrentTransportActions", [("InstanceID", "0")])
        self.assertEqual(actions["Actions"], "Pause,Stop")

    def other_service(self, sid):
        return ft.Renderer(self.r.key, self.r.name, "", "", "127.0.0.1", self.r.location,
                           f"http://127.0.0.1:{self.service.port}/{sid}/control",
                           rd.SERVICES[sid])

    def test_volume_goes_to_owntone_master_volume(self):
        rc = self.other_service("RenderingControl")
        ft.soap_call(rc, "SetVolume", [("InstanceID", "0"), ("Channel", "Master"),
                                       ("DesiredVolume", "30")])
        self.assertEqual(self.owntone_fake.requests[-1][:2],
                         ("PUT", "/api/player/volume?volume=30"))
        vol = ft.soap_call(rc, "GetVolume", [("InstanceID", "0"), ("Channel", "Master")])
        self.assertEqual(vol["CurrentVolume"], "40")  # what the fake OwnTone reports

    def test_protocol_info_lists_mp3_and_flac(self):
        cm = self.other_service("ConnectionManager")
        sink = ft.soap_call(cm, "GetProtocolInfo", [])["Sink"]
        self.assertIn("http-get:*:audio/mpeg:*", sink)
        self.assertIn("http-get:*:audio/flac:*", sink)

    def test_eventing_initial_and_on_change(self):
        events, got = [], threading.Event()

        class Catcher(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_NOTIFY(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                events.append((self.headers["SEQ"], body))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                got.set()

        catcher = ThreadingHTTPServer(("127.0.0.1", 0), Catcher)
        threading.Thread(target=catcher.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()
        self.addCleanup(catcher.server_close)
        self.addCleanup(catcher.shutdown)
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.service.port}/AVTransport/event", method="SUBSCRIBE",
            headers={"CALLBACK": f"<http://127.0.0.1:{catcher.server_port}/cb>",
                     "NT": "upnp:event", "TIMEOUT": "Second-300"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            self.assertTrue(resp.headers["SID"].startswith("uuid:"))
            self.assertEqual(resp.headers["TIMEOUT"], "Second-300")
        self.assertTrue(got.wait(3))
        self.assertEqual(events[0][0], "0")
        self.assertIn(b"NO_MEDIA_PRESENT", events[0][1])
        got.clear()
        ft.set_uri(self.r, URI, "")
        self.assertTrue(got.wait(3))
        self.assertEqual(events[-1][0], "1")
        self.assertIn(b"STOPPED", events[-1][1])


if __name__ == "__main__":
    unittest.main()
