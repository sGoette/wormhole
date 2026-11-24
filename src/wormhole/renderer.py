"""Minimal UPnP MediaRenderer: the turntable (or the fake turntable) pushes its stream here.

SetAVTransportURI + Play -> the stream URL goes to the shared PCM feed -> OwnTone's pipe.
Pause/Stop stop the feed (a live stream can't be paused); Play reconnects to the live stream.

What control points rely on is implemented: SSDP discovery and announcements, the device
description, AVTransport, RenderingControl (volume -> OwnTone master volume),
ConnectionManager (formats we accept) and basic GENA eventing (LastChange).
Standard library only; runs on the Mac and on the server.
"""

import logging
import platform
import random
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

from . import __version__
from .httpserver import QuietHTTPServer, worth_logging
from .owntone import OwnTone, OwnToneError

log = logging.getLogger(__name__)

SSDP_ADDR, SSDP_PORT = "239.255.255.250", 1900
MAX_AGE = 1800
# How often SSDP re-checks our LAN address (DHCP may still be running at boot, or renew).
ADDRESS_CHECK = 5.0
SERVER = f"{platform.system()}/{platform.release()} UPnP/1.0 wormhole/{__version__}"

DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RCS = "urn:schemas-upnp-org:service:RenderingControl:1"
CMS = "urn:schemas-upnp-org:service:ConnectionManager:1"
SERVICES = {"AVTransport": AVT, "RenderingControl": RCS, "ConnectionManager": CMS}

# What ffmpeg (feed.py) can decode from an HTTP stream.
SINK_MIME = ["audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac", "audio/wav",
             "audio/x-wav", "audio/aac", "audio/mp4", "audio/ogg",
             "audio/L16;rate=44100;channels=2", "audio/L16;rate=48000;channels=2"]
SINK_PROTOCOL_INFO = ",".join(f"http-get:*:{m}:*" for m in SINK_MIME)

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
COUNTER_NOT_IMPLEMENTED = "2147483647"  # AVTransport spec value for unsupported counters

# Shown by control apps that list renderers (rendered by tools/make_icons.py).
ICON_DIR = Path(__file__).resolve().parent / "frontend" / "icons"
ICON_SIZES = (48, 120, 256)
ICONS = {f"/icons/renderer-{s}.png": ICON_DIR / f"renderer-{s}.png" for s in ICON_SIZES}


def udn_for(name: str) -> str:
    """Stable per host + name: control points recognise us after a restart, and a new name
    is a new device for them (so none keeps showing the old name from its cache)."""
    seed = f"wormhole-renderer.{socket.gethostname()}.{name}"
    return "uuid:" + str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def local_ip_for(host: str) -> str:
    """Our address on the interface that reaches `host` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def primary_ip() -> str:
    return local_ip_for("192.0.2.1")  # TEST-NET address: picks the default-route interface


def usable_ip(ip: str) -> bool:
    """A LAN address control points can reach: not loopback, not link-local (169.254.x,
    which a DHCP client assigns itself while it has no lease yet)."""
    return not (ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0")


# --------------------------------------------------------------------------- service specs
# State variable: name -> (dataType, sendEvents, allowed values | ("range", min, max) | None)
# Action: name -> [(argument, "in"/"out", related state variable)]

IID = ("InstanceID", "in", "A_ARG_TYPE_InstanceID")

AVT_VARS = {
    "TransportState": ("string", False, ["STOPPED", "PLAYING", "PAUSED_PLAYBACK",
                                         "TRANSITIONING", "NO_MEDIA_PRESENT"]),
    "TransportStatus": ("string", False, ["OK", "ERROR_OCCURRED"]),
    "TransportPlaySpeed": ("string", False, ["1"]),
    "PlaybackStorageMedium": ("string", False, ["NETWORK", "NONE"]),
    "RecordStorageMedium": ("string", False, [NOT_IMPLEMENTED]),
    "PossiblePlaybackStorageMedia": ("string", False, None),
    "PossibleRecordStorageMedia": ("string", False, None),
    "PossibleRecordQualityModes": ("string", False, None),
    "RecordMediumWriteStatus": ("string", False, [NOT_IMPLEMENTED]),
    "CurrentRecordQualityMode": ("string", False, [NOT_IMPLEMENTED]),
    "CurrentPlayMode": ("string", False, ["NORMAL"]),
    "NumberOfTracks": ("ui4", False, ("range", 0, 1)),
    "CurrentTrack": ("ui4", False, ("range", 0, 1)),
    "CurrentTrackDuration": ("string", False, None),
    "CurrentMediaDuration": ("string", False, None),
    "CurrentTrackMetaData": ("string", False, None),
    "CurrentTrackURI": ("string", False, None),
    "AVTransportURI": ("string", False, None),
    "AVTransportURIMetaData": ("string", False, None),
    "NextAVTransportURI": ("string", False, None),
    "NextAVTransportURIMetaData": ("string", False, None),
    "RelativeTimePosition": ("string", False, None),
    "AbsoluteTimePosition": ("string", False, None),
    "RelativeCounterPosition": ("i4", False, None),
    "AbsoluteCounterPosition": ("i4", False, None),
    "CurrentTransportActions": ("string", False, None),
    "LastChange": ("string", True, None),
    "A_ARG_TYPE_SeekMode": ("string", False, ["REL_TIME", "TRACK_NR"]),
    "A_ARG_TYPE_SeekTarget": ("string", False, None),
    "A_ARG_TYPE_InstanceID": ("ui4", False, None),
}
AVT_ACTIONS = {
    "SetAVTransportURI": [IID, ("CurrentURI", "in", "AVTransportURI"),
                          ("CurrentURIMetaData", "in", "AVTransportURIMetaData")],
    "GetMediaInfo": [IID, ("NrTracks", "out", "NumberOfTracks"),
                     ("MediaDuration", "out", "CurrentMediaDuration"),
                     ("CurrentURI", "out", "AVTransportURI"),
                     ("CurrentURIMetaData", "out", "AVTransportURIMetaData"),
                     ("NextURI", "out", "NextAVTransportURI"),
                     ("NextURIMetaData", "out", "NextAVTransportURIMetaData"),
                     ("PlayMedium", "out", "PlaybackStorageMedium"),
                     ("RecordMedium", "out", "RecordStorageMedium"),
                     ("WriteStatus", "out", "RecordMediumWriteStatus")],
    "GetTransportInfo": [IID, ("CurrentTransportState", "out", "TransportState"),
                         ("CurrentTransportStatus", "out", "TransportStatus"),
                         ("CurrentSpeed", "out", "TransportPlaySpeed")],
    "GetPositionInfo": [IID, ("Track", "out", "CurrentTrack"),
                        ("TrackDuration", "out", "CurrentTrackDuration"),
                        ("TrackMetaData", "out", "CurrentTrackMetaData"),
                        ("TrackURI", "out", "CurrentTrackURI"),
                        ("RelTime", "out", "RelativeTimePosition"),
                        ("AbsTime", "out", "AbsoluteTimePosition"),
                        ("RelCount", "out", "RelativeCounterPosition"),
                        ("AbsCount", "out", "AbsoluteCounterPosition")],
    "GetDeviceCapabilities": [IID, ("PlayMedia", "out", "PossiblePlaybackStorageMedia"),
                              ("RecMedia", "out", "PossibleRecordStorageMedia"),
                              ("RecQualityModes", "out", "PossibleRecordQualityModes")],
    "GetTransportSettings": [IID, ("PlayMode", "out", "CurrentPlayMode"),
                             ("RecQualityMode", "out", "CurrentRecordQualityMode")],
    "GetCurrentTransportActions": [IID, ("Actions", "out", "CurrentTransportActions")],
    "Stop": [IID],
    "Play": [IID, ("Speed", "in", "TransportPlaySpeed")],
    "Pause": [IID],
    "Seek": [IID, ("Unit", "in", "A_ARG_TYPE_SeekMode"), ("Target", "in", "A_ARG_TYPE_SeekTarget")],
    "Next": [IID],
    "Previous": [IID],
}

RCS_VARS = {
    "PresetNameList": ("string", False, None),
    "LastChange": ("string", True, None),
    "Volume": ("ui2", False, ("range", 0, 100)),
    "Mute": ("boolean", False, None),
    "A_ARG_TYPE_Channel": ("string", False, ["Master"]),
    "A_ARG_TYPE_InstanceID": ("ui4", False, None),
    "A_ARG_TYPE_PresetName": ("string", False, ["FactoryDefaults"]),
}
CHANNEL = ("Channel", "in", "A_ARG_TYPE_Channel")
RCS_ACTIONS = {
    "ListPresets": [IID, ("CurrentPresetNameList", "out", "PresetNameList")],
    "SelectPreset": [IID, ("PresetName", "in", "A_ARG_TYPE_PresetName")],
    "GetVolume": [IID, CHANNEL, ("CurrentVolume", "out", "Volume")],
    "SetVolume": [IID, CHANNEL, ("DesiredVolume", "in", "Volume")],
    "GetMute": [IID, CHANNEL, ("CurrentMute", "out", "Mute")],
    "SetMute": [IID, CHANNEL, ("DesiredMute", "in", "Mute")],
}

CMS_VARS = {
    "SourceProtocolInfo": ("string", True, None),
    "SinkProtocolInfo": ("string", True, None),
    "CurrentConnectionIDs": ("string", True, None),
    "A_ARG_TYPE_ConnectionStatus": ("string", False, ["OK", "ContentFormatMismatch",
                                                      "InsufficientBandwidth",
                                                      "UnreliableChannel", "Unknown"]),
    "A_ARG_TYPE_ConnectionManager": ("string", False, None),
    "A_ARG_TYPE_Direction": ("string", False, ["Input", "Output"]),
    "A_ARG_TYPE_ProtocolInfo": ("string", False, None),
    "A_ARG_TYPE_ConnectionID": ("i4", False, None),
    "A_ARG_TYPE_AVTransportID": ("i4", False, None),
    "A_ARG_TYPE_RcsID": ("i4", False, None),
}
CMS_ACTIONS = {
    "GetProtocolInfo": [("Source", "out", "SourceProtocolInfo"),
                        ("Sink", "out", "SinkProtocolInfo")],
    "GetCurrentConnectionIDs": [("ConnectionIDs", "out", "CurrentConnectionIDs")],
    "GetCurrentConnectionInfo": [("ConnectionID", "in", "A_ARG_TYPE_ConnectionID"),
                                 ("RcsID", "out", "A_ARG_TYPE_RcsID"),
                                 ("AVTransportID", "out", "A_ARG_TYPE_AVTransportID"),
                                 ("ProtocolInfo", "out", "A_ARG_TYPE_ProtocolInfo"),
                                 ("PeerConnectionManager", "out", "A_ARG_TYPE_ConnectionManager"),
                                 ("PeerConnectionID", "out", "A_ARG_TYPE_ConnectionID"),
                                 ("Direction", "out", "A_ARG_TYPE_Direction"),
                                 ("Status", "out", "A_ARG_TYPE_ConnectionStatus")],
}

SPECS = {"AVTransport": (AVT_ACTIONS, AVT_VARS), "RenderingControl": (RCS_ACTIONS, RCS_VARS),
         "ConnectionManager": (CMS_ACTIONS, CMS_VARS)}


def scpd_xml(actions: Dict[str, list], variables: Dict[str, tuple]) -> bytes:
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<scpd xmlns="urn:schemas-upnp-org:service-1-0">',
             "<specVersion><major>1</major><minor>0</minor></specVersion><actionList>"]
    for name, args in actions.items():
        parts.append(f"<action><name>{name}</name>")
        if args:
            parts.append("<argumentList>")
            for arg, direction, var in args:
                parts.append(f"<argument><name>{arg}</name><direction>{direction}</direction>"
                             f"<relatedStateVariable>{var}</relatedStateVariable></argument>")
            parts.append("</argumentList>")
        parts.append("</action>")
    parts.append("</actionList><serviceStateTable>")
    for name, (dtype, events, allowed) in variables.items():
        parts.append(f'<stateVariable sendEvents="{"yes" if events else "no"}">'
                     f"<name>{name}</name><dataType>{dtype}</dataType>")
        if isinstance(allowed, tuple):
            parts.append(f"<allowedValueRange><minimum>{allowed[1]}</minimum>"
                         f"<maximum>{allowed[2]}</maximum><step>1</step></allowedValueRange>")
        elif allowed:
            parts.append("<allowedValueList>" + "".join(
                f"<allowedValue>{escape(v)}</allowedValue>" for v in allowed)
                + "</allowedValueList>")
        parts.append("</stateVariable>")
    parts.append("</serviceStateTable></scpd>")
    return "".join(parts).encode()


def description_xml(name: str, udn: str) -> bytes:
    services = "".join(
        f"<service><serviceType>{stype}</serviceType>"
        f"<serviceId>urn:upnp-org:serviceId:{sid}</serviceId>"
        f"<SCPDURL>/{sid}/scpd.xml</SCPDURL><controlURL>/{sid}/control</controlURL>"
        f"<eventSubURL>/{sid}/event</eventSubURL></service>"
        for sid, stype in SERVICES.items())
    # UPnP device description: mimetype, width, height, depth and url per icon
    icons = "".join(
        f"<icon><mimetype>image/png</mimetype><width>{s}</width><height>{s}</height>"
        f"<depth>32</depth><url>/icons/renderer-{s}.png</url></icon>" for s in ICON_SIZES)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<root xmlns="urn:schemas-upnp-org:device-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion><device>"
        f"<deviceType>{DEVICE_TYPE}</deviceType>"
        f"<friendlyName>{escape(name)}</friendlyName>"
        "<manufacturer>wormhole</manufacturer>"
        "<modelName>wormhole renderer</modelName>"
        f"<modelNumber>{__version__}</modelNumber>"
        "<modelDescription>UPnP renderer feeding OwnTone (AirPlay 2)</modelDescription>"
        f"<UDN>{udn}</UDN><iconList>{icons}</iconList><serviceList>{services}</serviceList>"
        "</device></root>"
    ).encode()


# --------------------------------------------------------------------------- SOAP

def content_type(url: str, timeout: float = 1.5) -> str:
    """Content-Type of `url` from a HEAD request (no audio is pulled), "" if it fails."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.headers.get("Content-Type", "")
    except (OSError, ValueError):
        return ""


def lossless_alternative(uri: str, port: Optional[int],
                         probe: Callable[[str], str] = content_type) -> Optional[str]:
    """The same host's FLAC stream on `port`, if it answers as FLAC; else None.

    The Pro-Ject T2 W pushes its MP3 stream (port 8145) to every renderer, and FLAC only to a
    built-in list of devices, but it serves the 24-bit/48 kHz FLAC stream on port 8146 to
    anyone who asks, whatever its lossless setting (undocumented, seen 2026-11-24).
    """
    parts = urllib.parse.urlparse(uri)
    if not port or parts.scheme != "http" or not parts.hostname or parts.port == port:
        return None
    candidate = f"http://{parts.hostname}:{port}/"
    return candidate if "flac" in probe(candidate).lower() else None


class UPnPFault(Exception):
    def __init__(self, code: int, description: str):
        super().__init__(f"{code} {description}")
        self.code, self.description = code, description


def parse_soap(body: bytes, soapaction: str) -> Tuple[str, Dict[str, str]]:
    root = ET.fromstring(body)
    body_el = next((el for el in root.iter() if el.tag.split("}")[-1] == "Body"), None)
    if body_el is None or len(body_el) == 0:
        raise UPnPFault(401, "Invalid Action")
    action_el = body_el[0]
    action = action_el.tag.split("}")[-1]
    header_action = soapaction.strip('"').rsplit("#", 1)[-1] if "#" in soapaction else ""
    if header_action and header_action != action:
        raise UPnPFault(401, f"SOAPACTION {header_action} does not match body {action}")
    return action, {child.tag.split("}")[-1]: (child.text or "") for child in action_el}


def soap_response(service_type: str, action: str, out: List[Tuple[str, str]]) -> bytes:
    inner = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in out)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action}Response xmlns:u="{service_type}">{inner}</u:{action}Response>'
        "</s:Body></s:Envelope>"
    ).encode()


def soap_fault(fault: UPnPFault) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><s:Fault>'
        "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{fault.code}</errorCode>"
        f"<errorDescription>{escape(fault.description)}</errorDescription>"
        "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    ).encode()


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# --------------------------------------------------------------------------- renderer state

class Renderer:
    """Transport state machine plus the SOAP action handlers."""

    def __init__(self, name: str, feed_control, owntone: Optional[OwnTone] = None,
                 udn: Optional[str] = None, pipe_name: str = "turntable",
                 lossless_port: Optional[int] = None):
        self.name = name
        self.lossless_port = lossless_port  # LOSSLESS_STREAM_PORT, see lossless_alternative()
        self.probe: Callable[[str], str] = content_type  # replaced in tests
        self.pipe_name = pipe_name  # file name of PCM_PIPE, to find it in OwnTone's library
        self.udn = udn or udn_for(name)
        self.feed = feed_control
        self.owntone = owntone
        self.lock = threading.RLock()
        self.uri, self.metadata = "", ""
        self.state = "NO_MEDIA_PRESENT"
        self.play_started = 0.0
        self.controller = ""  # IP of the last control point that sent a command
        self.volume, self.muted, self._volume_before_mute = 50, False, 50
        self.on_change: Callable[[str], None] = lambda service: None  # set by eventing

    # -- state helpers
    def transport_state(self) -> str:
        with self.lock:
            state = self.state
        if state == "PLAYING" and not self.feed.owned_by("upnp"):
            return "STOPPED"  # someone else (web UI) took over the feed
        return state

    def actions_available(self) -> str:
        state = self.transport_state()
        if state == "PLAYING":
            return "Pause,Stop"
        if state in ("STOPPED", "PAUSED_PLAYBACK"):
            return "Play,Stop"
        return ""

    def status(self) -> dict:
        return {"name": self.name, "state": self.transport_state(), "uri": self.uri,
                "controller": self.controller}

    def restart_stream(self) -> bool:
        """Reconnect to the live stream while PLAYING (resume after a pause without the audio
        that queued up in the meantime). False if nothing is playing from UPnP."""
        if self.transport_state() != "PLAYING" or not self.uri:
            return False
        self._start_feed()
        return True


    def _set_state(self, state: str) -> None:
        with self.lock:
            self.state = state
        log.info("renderer: %s %s", state, self.uri)
        self.on_change("AVTransport")

    def _owntone_play(self) -> None:
        """Called by the feeder when the first audio is there (errors -> feed last_error)."""
        if self.owntone is not None:
            self.owntone.play_pipe(self.pipe_name)

    def _owntone_stop(self) -> None:
        # The HomePods go silent right away instead of after OwnTone's buffer runs dry.
        if self.owntone is not None:
            try:
                self.owntone.stop()
            except OwnToneError as exc:
                log.warning("renderer: OwnTone stop failed: %s", exc)

    # -- dispatch
    def handle(self, service: str, action: str, args: Dict[str, str],
               client: str = "") -> List[Tuple[str, str]]:
        actions = SPECS[service][0]
        if action not in actions:
            raise UPnPFault(401, "Invalid Action")
        expected = [a for a, d, _ in actions[action] if d == "in"]
        missing = [a for a in expected if a not in args]
        if missing:
            raise UPnPFault(402, f"Invalid Args: missing {', '.join(missing)}")
        if "InstanceID" in expected and args["InstanceID"].strip() != "0":
            raise UPnPFault(718, "Invalid InstanceID")
        if client:
            self.controller = client
        return getattr(self, f"_{service}_{action}")(args)

    # -- AVTransport
    def _AVTransport_SetAVTransportURI(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        uri = args["CurrentURI"].strip()
        if not uri.startswith(("http://", "https://")):
            raise UPnPFault(716, f"Resource not found: only http(s) URIs are supported ({uri})")
        playing = self.transport_state() == "PLAYING"
        with self.lock:
            self.uri, self.metadata = uri, args.get("CurrentURIMetaData", "")
        if playing:  # spec: a new URI while playing starts playing it
            self._start_feed()
        else:
            self._set_state("STOPPED")
        return []

    def _start_feed(self) -> None:
        try:
            source = lossless_alternative(self.uri, self.lossless_port, self.probe)
            if source:
                log.info("renderer: playing the lossless stream %s instead of %s",
                         source, self.uri)
            # OwnTone starts when the first audio arrives, not now: a FLAC stream needs ~3 s,
            # longer than OwnTone's start buffer, and the HomePods then stay silent.
            self.feed.start(source or self.uri, origin="upnp",
                            on_first_data=self._owntone_play)
        except (ValueError, SystemExit) as exc:
            raise UPnPFault(716, f"Resource not found: {exc}") from None
        with self.lock:
            self.play_started = time.time()
        self._set_state("PLAYING")

    def _AVTransport_Play(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        if not self.uri:
            raise UPnPFault(701, "Transition not available: no URI set")
        if args.get("Speed", "1") not in ("1", ""):
            raise UPnPFault(717, "Play speed not supported")
        if self.transport_state() != "PLAYING":
            self._start_feed()
        return []

    def _AVTransport_Pause(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        if self.transport_state() != "PLAYING":
            raise UPnPFault(701, "Transition not available")
        self.feed.stop(origin="upnp")
        self._owntone_stop()
        self._set_state("PAUSED_PLAYBACK")
        return []

    def _AVTransport_Stop(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        was_playing = self.feed.owned_by("upnp")
        self.feed.stop(origin="upnp")
        if was_playing:
            self._owntone_stop()
        self._set_state("STOPPED" if self.uri else "NO_MEDIA_PRESENT")
        return []

    def _AVTransport_GetTransportInfo(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        return [("CurrentTransportState", self.transport_state()),
                ("CurrentTransportStatus", "OK"), ("CurrentSpeed", "1")]

    def _AVTransport_GetMediaInfo(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        return [("NrTracks", "1" if self.uri else "0"), ("MediaDuration", "00:00:00"),
                ("CurrentURI", self.uri), ("CurrentURIMetaData", self.metadata),
                ("NextURI", ""), ("NextURIMetaData", ""),
                ("PlayMedium", "NETWORK" if self.uri else "NONE"),
                ("RecordMedium", NOT_IMPLEMENTED), ("WriteStatus", NOT_IMPLEMENTED)]

    def _AVTransport_GetPositionInfo(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        playing = self.transport_state() == "PLAYING"
        rel = hms(time.time() - self.play_started) if playing else "0:00:00"
        return [("Track", "1" if self.uri else "0"), ("TrackDuration", "00:00:00"),
                ("TrackMetaData", self.metadata), ("TrackURI", self.uri),
                ("RelTime", rel), ("AbsTime", NOT_IMPLEMENTED),
                ("RelCount", COUNTER_NOT_IMPLEMENTED), ("AbsCount", COUNTER_NOT_IMPLEMENTED)]

    def _AVTransport_GetDeviceCapabilities(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        return [("PlayMedia", "NETWORK"), ("RecMedia", NOT_IMPLEMENTED),
                ("RecQualityModes", NOT_IMPLEMENTED)]

    def _AVTransport_GetTransportSettings(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        return [("PlayMode", "NORMAL"), ("RecQualityMode", NOT_IMPLEMENTED)]

    def _AVTransport_GetCurrentTransportActions(self, args) -> List[Tuple[str, str]]:
        return [("Actions", self.actions_available())]

    def _AVTransport_Seek(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        raise UPnPFault(710, "Seek mode not supported (live stream)")

    def _AVTransport_Next(self, args: Dict[str, str]) -> List[Tuple[str, str]]:
        raise UPnPFault(701, "Transition not available")

    _AVTransport_Previous = _AVTransport_Next

    # -- RenderingControl (volume = OwnTone master volume)
    def _current_volume(self) -> int:
        if self.owntone is not None and not self.muted:
            try:
                self.volume = int(self.owntone.player().get("volume", self.volume))
            except (OwnToneError, TypeError, ValueError):
                pass
        return 0 if self.muted else self.volume

    def _apply_volume(self, volume: int) -> None:
        if self.owntone is not None:
            try:
                self.owntone.set_master_volume(volume)
            except OwnToneError as exc:
                raise UPnPFault(501, f"Action Failed: {exc}") from None

    def _RenderingControl_ListPresets(self, args) -> List[Tuple[str, str]]:
        return [("CurrentPresetNameList", "FactoryDefaults")]

    def _RenderingControl_SelectPreset(self, args) -> List[Tuple[str, str]]:
        if args["PresetName"] != "FactoryDefaults":
            raise UPnPFault(701, "Invalid Name")
        return []

    def _RenderingControl_GetVolume(self, args) -> List[Tuple[str, str]]:
        return [("CurrentVolume", str(self._current_volume()))]

    def _RenderingControl_SetVolume(self, args) -> List[Tuple[str, str]]:
        try:
            volume = int(args["DesiredVolume"])
        except ValueError:
            raise UPnPFault(402, "Invalid Args: DesiredVolume") from None
        if not 0 <= volume <= 100:
            raise UPnPFault(402, "Invalid Args: DesiredVolume must be 0-100")
        self._apply_volume(volume)
        self.volume, self.muted = volume, False
        self.on_change("RenderingControl")
        return []

    def _RenderingControl_GetMute(self, args) -> List[Tuple[str, str]]:
        return [("CurrentMute", "1" if self.muted else "0")]

    def _RenderingControl_SetMute(self, args) -> List[Tuple[str, str]]:
        mute = args["DesiredMute"].strip().lower() in ("1", "true", "yes")
        if mute and not self.muted:
            self._volume_before_mute = self._current_volume()
            self._apply_volume(0)
        elif not mute and self.muted:
            self._apply_volume(self._volume_before_mute)
            self.volume = self._volume_before_mute
        self.muted = mute
        self.on_change("RenderingControl")
        return []

    # -- ConnectionManager
    def _ConnectionManager_GetProtocolInfo(self, args) -> List[Tuple[str, str]]:
        return [("Source", ""), ("Sink", SINK_PROTOCOL_INFO)]

    def _ConnectionManager_GetCurrentConnectionIDs(self, args) -> List[Tuple[str, str]]:
        return [("ConnectionIDs", "0")]

    def _ConnectionManager_GetCurrentConnectionInfo(self, args) -> List[Tuple[str, str]]:
        if args["ConnectionID"].strip() != "0":
            raise UPnPFault(706, "Invalid connection reference")
        return [("RcsID", "0"), ("AVTransportID", "0"), ("ProtocolInfo", ""),
                ("PeerConnectionManager", ""), ("PeerConnectionID", "-1"),
                ("Direction", "Input"), ("Status", "OK")]

    # -- eventing payloads
    def event_properties(self, service: str) -> List[Tuple[str, str]]:
        if service == "AVTransport":
            fields = [("TransportState", self.transport_state()),
                      ("TransportStatus", "OK"),
                      ("AVTransportURI", self.uri),
                      ("CurrentTrackURI", self.uri),
                      ("CurrentTransportActions", self.actions_available())]
            inner = "".join(f"<{k} val={quoteattr(v)}/>" for k, v in fields)
            return [("LastChange", '<Event xmlns="urn:schemas-upnp-org:metadata-1-0/AVT/">'
                                   f'<InstanceID val="0">{inner}</InstanceID></Event>')]
        if service == "RenderingControl":
            volume = 0 if self.muted else self.volume
            return [("LastChange", '<Event xmlns="urn:schemas-upnp-org:metadata-1-0/RCS/">'
                                   f'<InstanceID val="0"><Volume channel="Master" val="{volume}"/>'
                                   f'<Mute channel="Master" val="{int(self.muted)}"/>'
                                   "</InstanceID></Event>")]
        return [("SourceProtocolInfo", ""), ("SinkProtocolInfo", SINK_PROTOCOL_INFO),
                ("CurrentConnectionIDs", "0")]


# --------------------------------------------------------------------------- GENA eventing

def propertyset_xml(props: List[Tuple[str, str]]) -> bytes:
    inner = "".join(f"<e:property><{k}>{escape(v)}</{k}></e:property>" for k, v in props)
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">{inner}'
            "</e:propertyset>").encode()


class Subscriptions:
    def __init__(self, renderer: Renderer):
        self.renderer = renderer
        self.lock = threading.Lock()
        self.subs: Dict[str, dict] = {}  # sid -> {service, callbacks, seq, expires}

    def subscribe(self, service: str, callbacks: List[str], timeout: int) -> str:
        sid = "uuid:" + str(uuid.uuid4())
        with self.lock:
            self.subs[sid] = {"service": service, "callbacks": callbacks, "seq": 0,
                              "expires": time.time() + timeout}
        # Initial event must follow the SUBSCRIBE response.
        threading.Timer(0.2, self._send, args=(sid,)).start()
        return sid

    def renew(self, sid: str, timeout: int) -> bool:
        with self.lock:
            if sid not in self.subs:
                return False
            self.subs[sid]["expires"] = time.time() + timeout
            return True

    def unsubscribe(self, sid: str) -> bool:
        with self.lock:
            return self.subs.pop(sid, None) is not None

    def clear(self) -> None:
        with self.lock:
            self.subs.clear()

    def notify(self, service: str) -> None:
        with self.lock:
            now = time.time()
            for sid in [s for s, sub in self.subs.items() if sub["expires"] < now]:
                del self.subs[sid]
            sids = [s for s, sub in self.subs.items() if sub["service"] == service]
        for sid in sids:
            threading.Thread(target=self._send, args=(sid,), daemon=True).start()

    def _send(self, sid: str) -> None:
        with self.lock:
            sub = self.subs.get(sid)
            if sub is None:
                return
            seq = sub["seq"]
            sub["seq"] = seq + 1 if seq < 4294967295 else 1
            service, callbacks = sub["service"], sub["callbacks"]
        body = propertyset_xml(self.renderer.event_properties(service))
        for url in callbacks:  # spec: try each callback until one accepts
            req = urllib.request.Request(url, data=body, method="NOTIFY", headers={
                "Content-Type": 'text/xml; charset="utf-8"', "NT": "upnp:event",
                "NTS": "upnp:propchange", "SID": sid, "SEQ": str(seq)})
            try:
                with urllib.request.urlopen(req, timeout=3):
                    return
            except OSError as exc:
                log.debug("event to %s failed: %s", url, exc)
        log.info("renderer: dropping subscription %s (no callback reachable)", sid)
        self.unsubscribe(sid)


# --------------------------------------------------------------------------- HTTP

def make_handler(renderer: Renderer, subs: Subscriptions) -> type:
    paths = {f"/{sid}/{kind}": (sid, kind) for sid in SERVICES
             for kind in ("scpd.xml", "control", "event")}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = SERVER

        def log_message(self, fmt: str, *args) -> None:
            log.debug("renderer http %s %s", self.address_string(), fmt % args)

        def log_request(self, code: Any = "-", size: Any = "-") -> None:
            # Unlike the web UI the renderer gets few requests, and which ones a control point
            # makes shows what it checks before picking a stream (e.g. MP3 or FLAC): all of
            # them at debug, failures as before.
            log.debug("renderer http %s %s %s -> %s (User-Agent: %s)", self.address_string(),
                      self.command, self.path, code, self.headers.get("User-Agent", "-"))
            if worth_logging(code):
                super().log_request(code, size)

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path == "/description.xml":
                return self._send(200, description_xml(renderer.name, renderer.udn))
            if path in ICONS:
                try:
                    return self._send(200, ICONS[path].read_bytes(),
                                      content_type="image/png")
                except OSError:
                    return self._send(404, b"")
            sid, kind = paths.get(path, ("", ""))
            if kind == "scpd.xml":
                return self._send(200, scpd_xml(*SPECS[sid]))
            self._send(404, b"")

        def do_POST(self) -> None:
            sid, kind = paths.get(self.path, ("", ""))
            body = self._read_body()
            if kind != "control":
                return self._send(404, b"")
            stype = SERVICES[sid]
            try:
                action, args = parse_soap(body, self.headers.get("SOAPACTION", ""))
                out = renderer.handle(sid, action, args, client=self.client_address[0])
            except ET.ParseError:
                return self._send(500, soap_fault(UPnPFault(401, "Invalid Action")))
            except UPnPFault as fault:
                log.info("renderer: %s fault %s", sid, fault)
                return self._send(500, soap_fault(fault))
            log.debug("renderer: %s %s %s", sid, action, args)
            self._send(200, soap_response(stype, action, out))

        def do_SUBSCRIBE(self) -> None:
            sid_name, kind = paths.get(self.path, ("", ""))
            self._read_body()
            if kind != "event":
                return self._send(404, b"")
            timeout = self._timeout()
            existing = self.headers.get("SID")
            if existing:
                if not subs.renew(existing, timeout):
                    return self._send(412, b"")
                return self._send(200, b"", {"SID": existing, "TIMEOUT": f"Second-{timeout}"})
            callbacks = [c.strip("<> ") for c in self.headers.get("CALLBACK", "").split(">")
                         if c.strip("<> ")]
            if self.headers.get("NT") != "upnp:event" or not callbacks:
                return self._send(412, b"")
            sid = subs.subscribe(sid_name, callbacks, timeout)
            self._send(200, b"", {"SID": sid, "TIMEOUT": f"Second-{timeout}"})

        def do_UNSUBSCRIBE(self) -> None:
            self._read_body()
            ok = subs.unsubscribe(self.headers.get("SID", ""))
            self._send(200 if ok else 412, b"")

        def _timeout(self) -> int:
            value = self.headers.get("TIMEOUT", "Second-1800").split("-")[-1]
            return min(int(value), 86400) if value.isdigit() else 1800

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _send(self, status: int, body: bytes, headers: Optional[dict] = None,
                  content_type: str = 'text/xml; charset="utf-8"') -> None:
            self.send_response(status)
            if body:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("EXT", "")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

    return Handler


# --------------------------------------------------------------------------- SSDP

def ssdp_targets(udn: str) -> List[str]:
    return ["upnp:rootdevice", udn, DEVICE_TYPE] + list(SERVICES.values())


def usn_for(udn: str, target: str) -> str:
    return udn if target == udn else f"{udn}::{target}"


def matching_targets(st: str, udn: str) -> List[str]:
    targets = ssdp_targets(udn)
    if st == "ssdp:all":
        return targets
    return [st] if st in targets else []


def search_response(st: str, udn: str, location: str) -> bytes:
    return (
        "HTTP/1.1 200 OK\r\n"
        f"CACHE-CONTROL: max-age={MAX_AGE}\r\n"
        f"DATE: {formatdate(usegmt=True)}\r\n"
        "EXT:\r\n"
        f"LOCATION: {location}\r\n"
        f"SERVER: {SERVER}\r\n"
        f"ST: {st}\r\n"
        f"USN: {usn_for(udn, st)}\r\n"
        "\r\n"
    ).encode()


def notify_message(target: str, udn: str, location: str, alive: bool) -> bytes:
    lines = ["NOTIFY * HTTP/1.1", f"HOST: {SSDP_ADDR}:{SSDP_PORT}", f"NT: {target}",
             f"NTS: ssdp:{'alive' if alive else 'byebye'}", f"USN: {usn_for(udn, target)}"]
    if alive:
        lines += [f"CACHE-CONTROL: max-age={MAX_AGE}", f"LOCATION: {location}",
                  f"SERVER: {SERVER}"]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def parse_request(data: bytes) -> Tuple[str, Dict[str, str]]:
    lines = data.decode("utf-8", "replace").split("\r\n")
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().upper()] = value.strip()
    return lines[0], headers


class SSDP:
    """Discovery on the default-route interface. The address is re-checked every few
    seconds: at boot the network may not be up yet when we start, and a DHCP lease can
    change later; without that we would stay joined on loopback and nobody finds us."""

    def __init__(self, udn: str, http_port: int, host_ip: Optional[str] = None,
                 detect: Callable[[], str] = primary_ip):
        self.udn, self.http_port, self.host_ip = udn, http_port, host_ip
        self.detect = detect
        self.iface: Optional[str] = None  # address the multicast group is joined on
        self.stopping = threading.Event()
        self.sock: Optional[socket.socket] = None
        self._waiting_logged = False

    def location(self, peer: Optional[str] = None) -> str:
        ip = self.host_ip or (local_ip_for(peer) if peer else self.iface or self.detect())
        return f"http://{ip}:{self.http_port}/description.xml"

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", SSDP_PORT))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(1.0)
        self.sock = sock
        self.check_address()
        threading.Thread(target=self._listen, name="ssdp", daemon=True).start()
        threading.Thread(target=self._announce, name="ssdp-notify", daemon=True).start()
        if not self.host_ip:
            threading.Thread(target=self._watch_address, name="ssdp-address",
                             daemon=True).start()

    def stop(self) -> None:
        self.stopping.set()
        if self.sock is not None:
            self._notify_all(alive=False)
            self.sock.close()

    def check_address(self) -> bool:
        """Join the multicast group on our current LAN address if it changed.
        Returns True when it (re)joined, so the caller announces us."""
        ip = self.host_ip or self.detect()
        if ip == self.iface:
            return False
        if not self.host_ip and not usable_ip(ip):
            if not self._waiting_logged:
                log.warning("renderer: no LAN address yet (have %s), SSDP waits for one", ip)
                self._waiting_logged = True
            return False
        self._waiting_logged = False
        if self.iface is not None:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP,
                                     self._mreq(self.iface))
            except OSError:
                pass  # the old address is gone, and its membership with it
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self._mreq(ip))
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                 socket.inet_aton(ip))
        except OSError as exc:
            log.warning("renderer: cannot join SSDP on %s: %s", ip, exc)
            self.iface = None
            return False
        old, self.iface = self.iface, ip
        if old is None:
            log.info("renderer: SSDP on %s, description %s", ip, self.location())
        else:
            log.info("renderer: address changed %s -> %s, SSDP moved", old, ip)
        return True

    @staticmethod
    def _mreq(ip: str) -> bytes:
        return struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(ip))

    def _watch_address(self) -> None:
        while not self.stopping.wait(ADDRESS_CHECK):
            if self.check_address():
                self._burst()

    def _notify_all(self, alive: bool) -> None:
        if self.iface is None:
            return  # not on the LAN yet; we announce once we are
        for target in ssdp_targets(self.udn):
            try:
                self.sock.sendto(notify_message(target, self.udn, self.location(), alive),
                                 (SSDP_ADDR, SSDP_PORT))
            except OSError as exc:
                log.debug("ssdp notify failed: %s", exc)

    def change_udn(self, udn: str) -> None:
        """Say goodbye as the old device, then announce the new one."""
        if self.sock is not None:
            self._notify_all(alive=False)
        self.udn = udn
        if self.sock is not None:
            threading.Thread(target=self._burst, name="ssdp-notify", daemon=True).start()

    def _burst(self) -> None:
        # Three quick rounds (UDP may drop).
        for delay in (0.1, 1.0, 2.0):
            if self.stopping.wait(delay):
                return
            self._notify_all(alive=True)

    def _announce(self) -> None:
        self._burst()
        # Then well within max-age.
        while not self.stopping.wait(MAX_AGE / 3):
            self._notify_all(alive=True)

    def _listen(self) -> None:
        while not self.stopping.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            start, headers = parse_request(data)
            if not start.startswith("M-SEARCH") or headers.get("MAN", "") != '"ssdp:discover"':
                continue
            targets = matching_targets(headers.get("ST", ""), self.udn)
            if targets:
                log.debug("ssdp: %s searches %s (User-Agent: %s)", addr[0], headers.get("ST"),
                          headers.get("USER-AGENT", "-"))
                mx = headers.get("MX", "1")
                delay = random.uniform(0, min(int(mx) if mx.isdigit() else 1, 2) / 2)
                threading.Timer(delay, self._reply, args=(targets, addr)).start()

    def _reply(self, targets: List[str], addr: Tuple[str, int]) -> None:
        location = self.location(addr[0])
        for target in targets:
            try:
                self.sock.sendto(search_response(target, self.udn, location), addr)
            except OSError as exc:
                log.debug("ssdp reply to %s failed: %s", addr, exc)


# --------------------------------------------------------------------------- service

class RendererService:
    """Renderer + HTTP server + SSDP + eventing, started from `wormhole web`."""

    def __init__(self, name: str, port: int, feed_control, owntone: Optional[OwnTone],
                 host_ip: Optional[str] = None, ssdp: bool = True,
                 pipe_name: str = "turntable", lossless_port: Optional[int] = None):
        self.renderer = Renderer(name, feed_control, owntone, pipe_name=pipe_name,
                                 lossless_port=lossless_port)
        self.subs = Subscriptions(self.renderer)
        self.renderer.on_change = self.subs.notify
        self.http = QuietHTTPServer(("", port), make_handler(self.renderer, self.subs))
        self.port = self.http.server_port
        self.ssdp = SSDP(self.renderer.udn, self.port, host_ip) if ssdp else None
        self._stopping = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.1},
                         name="renderer-http", daemon=True).start()
        if self.ssdp is not None:
            self.ssdp.start()
        threading.Thread(target=self._watch, name="renderer-watch", daemon=True).start()
        log.info("renderer '%s' (%s) on port %d", self.renderer.name, self.renderer.udn,
                 self.port)

    def rename(self, name: str) -> None:
        """New friendly name, live: control points see the old device leave and a new one
        (new UDN) appear. Playback keeps running; subscriptions belonged to the old device."""
        old = self.renderer.name
        with self.renderer.lock:
            self.renderer.name = name
            self.renderer.udn = udn_for(name)
        self.subs.clear()
        if self.ssdp is not None:
            self.ssdp.change_udn(self.renderer.udn)
        log.info("renderer renamed: '%s' -> '%s' (%s)", old, name, self.renderer.udn)

    def stop(self) -> None:
        self._stopping.set()
        if self.ssdp is not None:
            self.ssdp.stop()
        self.http.shutdown()
        self.http.server_close()

    def _watch(self) -> None:
        """Send an event when the state changes behind our back (web UI took the feed)."""
        last = self.renderer.transport_state()
        while not self._stopping.wait(2):
            now = self.renderer.transport_state()
            if now != last:
                self.subs.notify("AVTransport")
                last = now
