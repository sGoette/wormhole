"""Minimal client for OwnTone's JSON API.

Endpoints and fields follow docs/json-api.md in the OwnTone repo (checked against 29.3):
GET /api/outputs, PUT /api/outputs/set, PUT /api/outputs/{id}, GET /api/player,
PUT /api/player/{play,pause,stop}.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class OwnToneError(Exception):
    pass


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
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

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
            raise OwnToneError(f"{method} {path}: HTTP {exc.code} {detail}") from None
        except OSError as exc:
            raise OwnToneError(f"cannot reach OwnTone at {self.base_url}: {exc}") from None
        return json.loads(raw) if raw else None

    # -- outputs (speakers)
    def outputs(self) -> List[Output]:
        return [Output.from_json(o) for o in self._request("GET", "/api/outputs")["outputs"]]

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

    def play(self) -> None:
        self._request("PUT", "/api/player/play")

    def pause(self) -> None:
        self._request("PUT", "/api/player/pause")

    def stop(self) -> None:
        self._request("PUT", "/api/player/stop")
