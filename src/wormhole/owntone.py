"""Minimal client for OwnTone's JSON API.

Endpoints and fields follow docs/json-api.md in the OwnTone repo (checked against 29.4):
GET /api/outputs, PUT /api/outputs/set, PUT /api/outputs/{id}, GET /api/player,
PUT /api/player/{play,pause,stop}, PUT /api/queue/items/{id} (artwork_url, title, artist).
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


log = logging.getLogger(__name__)


class OwnToneError(Exception):
    pass


# "[2026-11-13 11:21:11] [  LOG] [   player (9792)]  airplay: Response to GET /info ..."
LOG_LINE = re.compile(
    r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[\s*(FATAL|LOG|WARN)\] \[[^\]]*\]\s+(\w+): (.*)$")


def log_problems_since(log_path: Path, since: float, limit: int = 4) -> List[str]:
    """OwnTone log lines at WARN or worse since `since` (epoch). OwnTone's API only answers
    "400 Bad Request" on failures; the reason is in its log."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        m = LOG_LINE.match(line)
        if not m or "JSON api request failed" in line:
            continue
        stamp = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))  # local time
        if stamp >= int(since) - 1:
            found.append(f"{m.group(3)}: {m.group(4).strip()}")
    return found[-limit:]


@dataclass
class Output:
    id: str
    name: str
    type: str
    selected: bool
    volume: int
    requires_auth: bool
    needs_auth_key: bool

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Output":
        return cls(
            id=str(d["id"]),
            name=d.get("name", ""),
            type=d.get("type", ""),
            selected=bool(d.get("selected")),
            volume=int(d.get("volume") or 0),
            requires_auth=bool(d.get("requires_auth")),
            needs_auth_key=bool(d.get("needs_auth_key")),
        )


class OwnTone:
    def __init__(self, base_url: str, timeout: float = 5.0, artwork: Optional[Path] = None,
                 title: str = "", artist: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # "Now playing" for the pipe: image (as OwnTone sees the path), title and artist.
        self.artwork, self.title, self.artist = artwork, title, artist

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                detail = exc.read()[:300].decode("utf-8", "replace").strip()
            title = re.search(r"<h1>(.*?)</h1>", detail, re.S)  # OwnTone sends an HTML page
            detail = title.group(1).strip() if title else detail
            raise OwnToneError(f"{method} {path}: HTTP {exc.code} {detail}") from None
        except OSError as exc:
            raise OwnToneError(f"cannot reach OwnTone at {self.base_url}: {exc}") from None
        return json.loads(raw) if raw else None

    # -- outputs (speakers)
    def outputs(self) -> List[Output]:
        return [Output.from_json(o) for o in self._request("GET", "/api/outputs")["outputs"]]

    def output(self, output_id: str) -> Output:
        return Output.from_json(self._request("GET", f"/api/outputs/{output_id}"))

    def set_outputs(self, ids: List[str]) -> None:
        """Enable exactly these outputs; all others are disabled."""
        self._request("PUT", "/api/outputs/set", {"outputs": ids})

    def update_output(self, output_id: str, selected: Optional[bool] = None,
                      volume: Optional[int] = None, pin: Optional[str] = None) -> None:
        body: Dict[str, Any] = {}
        if selected is not None:
            body["selected"] = selected
        if volume is not None:
            body["volume"] = volume
        if pin is not None:
            body["pin"] = pin
        self._request("PUT", f"/api/outputs/{output_id}", body)

    def find_outputs(self, names: List[str]) -> List[Output]:
        """Match each name exactly or as a unique case-insensitive substring."""
        available = self.outputs()
        found = []
        for name in names:
            exact = [o for o in available if o.name.lower() == name.lower() or o.id == name]
            matches = exact or [o for o in available if name.lower() in o.name.lower()]
            if len(matches) != 1:
                why = "ambiguous" if matches else "no output matches"
                listed = ", ".join(o.name for o in (matches or available)) or "none"
                raise OwnToneError(f"{why} {name!r}; outputs: {listed}")
            found.append(matches[0])
        return found

    # -- player
    def player(self) -> Dict[str, Any]:
        return self._request("GET", "/api/player")

    def pipe_uri(self, name: str) -> str:
        """Library URI of the named pipe called `name` (file name, not full path: the path
        OwnTone sees can differ from ours, e.g. on another host)."""
        expr = urllib.parse.quote("data_kind is pipe")
        items = self._request("GET", f"/api/search?type=tracks&expression={expr}")
        pipes = items.get("tracks", {}).get("items", [])
        named = [t for t in pipes if Path(t.get("path", "")).name == name]
        if len(named) == 1:
            return named[0]["uri"]
        found = ", ".join(t.get("path", "?") for t in pipes) or "none"
        raise OwnToneError(f"no unique pipe named {name!r} in the OwnTone library "
                           f"(pipes found: {found}); is it inside a library directory?")

    def play_pipe(self, name: str) -> None:
        """Queue the pipe as a fresh item with our "now playing" info and start it.

        wormhole starts the pipe itself; OwnTone's pipe_autostart is off in the config,
        because an autostarted item has none of our metadata (title = file name, no artwork;
        seen 2026-11-24 after a record change). Started before data arrives, OwnTone waits
        (state 'pause') and plays as soon as data flows.
        """
        uri = urllib.parse.quote(self.pipe_uri(name), safe=":")
        query = self.metadata_query()
        if not query:
            self._request("POST", f"/api/queue/items/add?uris={uri}&clear=true&playback=start")
            return
        # OwnTone sends metadata to the speakers when playback starts (player.c,
        # event_play_start), so it goes on the queue item before Play.
        reply = self._request("POST", f"/api/queue/items/add?uris={uri}&clear=true") or {}
        items = reply.get("items") or []
        if items:
            try:
                self._request("PUT", f"/api/queue/items/{items[0]['id']}?{query}")
            except OwnToneError as exc:  # no artwork is no reason to stay silent
                log.warning("owntone: setting the now playing info failed: %s", exc)
        self.play()

    def metadata_query(self) -> str:
        """Query for PUT /api/queue/items/{id}. For pipes, OwnTone (29.3, 29.4) reads a
        `file:` artwork_url itself (artwork.c, source "pipe"; needs
        `artwork_individual = true`) and scales it to what the device asks for."""
        params = []
        if self.artwork is not None:
            params.append(("artwork_url", f"file:{self.artwork}"))
        if self.title:
            params.append(("title", self.title))
        if self.artist:
            params.append(("artist", self.artist))
        return urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe=":/")

    def set_master_volume(self, volume: int) -> None:
        self._request("PUT", f"/api/player/volume?volume={int(volume)}")

    def play(self) -> None:
        self._request("PUT", "/api/player/play")

    def pause(self) -> None:
        self._request("PUT", "/api/player/pause")

    def stop(self) -> None:
        """Stop playback *and* clear the queue.

        A plain stop leaves the pipe as the player's current item. OwnTone's pipe autostart
        (pipe.c, pipe_read_cb) then treats new data as "already playing" and never restarts,
        so the next Play stays silent. Clearing the queue resets the current item.
        Verified against OwnTone 29.3 on 2026-11-13.
        """
        self._request("PUT", "/api/player/stop")
        self._request("PUT", "/api/queue/clear")
