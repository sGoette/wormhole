"""Album search and cover download for the record library, from interchangeable sources.

A source is a small class with `search()` and `cover_urls()`, registered in SOURCES; the
setting COVER_SOURCES picks which ones the web UI offers (first = default). Adding another
one (Discogs, Deezer, ...) means one more class here, nothing else.
"""

import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__
from .pipemeta import PICTURE_MAX

log = logging.getLogger(__name__)

SEARCH_LIMIT = 15
COVER_BYTES_MAX = PICTURE_MAX  # covers go to OwnTone's metadata pipe as they are
MACOS_CERTS = "/etc/ssl/cert.pem"


class CoverError(Exception):
    """A source failed (network, answer, no cover): the message is shown in the web UI."""


@dataclass
class Album:
    """One search result. `ref` is the source's own id, used again to fetch the cover."""
    source: str
    ref: str
    title: str
    artist: str
    year: str = ""
    kind: str = ""   # e.g. "Album · Live", "12 tracks"
    thumb: str = ""  # small cover for the result list, loaded by the browser itself

    def to_json(self) -> dict:
        return asdict(self)


def image_kind(data: bytes) -> Optional[str]:
    """'jpg' or 'png' from the first bytes (the formats OwnTone's metadata pipe takes)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


# fetch(url, headers, timeout) -> (status, body); raises OSError when there's no answer.
Fetch = Callable[[str, Dict[str, str], float], Tuple[int, bytes]]


_tls: Optional[ssl.SSLContext] = None


def tls() -> ssl.SSLContext:
    """Default certificates, plus macOS's own bundle: python.org's Python on the Mac comes
    without any (Debian's come from the ca-certificates package)."""
    global _tls
    if _tls is None:
        ctx = ssl.create_default_context()
        if os.path.isfile(MACOS_CERTS):
            ctx.load_verify_locations(MACOS_CERTS)
        _tls = ctx
    return _tls


def http_get(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=tls()) as resp:
            return resp.status, resp.read(COVER_BYTES_MAX * 3)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read(4096)


class CoverSource:
    name = ""
    label = ""
    timeout = 10.0

    def __init__(self, cfg: Dict[str, str], fetch: Fetch = http_get):
        self.cfg, self.fetch = cfg, fetch
        contact = cfg.get("COVER_CONTACT", "").strip()
        # MusicBrainz asks for a way to reach the maintainer; nothing is sent unless set.
        self.user_agent = f"wormhole/{__version__}" + (f" ( {contact} )" if contact else "")

    def search(self, album: str, artist: str, limit: int = SEARCH_LIMIT) -> List[Album]:
        raise NotImplementedError

    def cover_urls(self, ref: str) -> List[str]:
        """Cover URLs for a search result, biggest first."""
        raise NotImplementedError

    def get(self, url: str) -> Tuple[int, bytes]:
        try:
            return self.fetch(url, {"User-Agent": self.user_agent}, self.timeout)
        except OSError as exc:
            raise CoverError(f"{self.label} is not reachable: {exc}") from None

    def get_json(self, url: str) -> dict:
        status, body = self.get(url)
        if status != 200:
            raise CoverError(f"{self.label} answered HTTP {status}")
        try:
            data = json.loads(body)
        except ValueError:
            raise CoverError(f"{self.label} sent an unreadable answer") from None
        if not isinstance(data, dict):
            raise CoverError(f"{self.label} sent an unexpected answer")
        return data

    def cover(self, ref: str) -> bytes:
        """The biggest cover that OwnTone can take (see COVER_BYTES_MAX)."""
        for url in self.cover_urls(ref):
            status, body = self.get(url)
            if status == 404:
                break  # no cover at all; the smaller sizes are made from the same image
            if status == 200 and image_kind(body) and len(body) <= COVER_BYTES_MAX:
                return body
            log.debug("cover %s: HTTP %s, %d bytes, not used", url, status, len(body))
        raise CoverError(f"{self.label} has no cover for this album")


def lucene(text: str) -> str:
    """User text as plain words for a Lucene query (MusicBrainz search syntax)."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", " ".join(text.split()))


class MusicBrainz(CoverSource):
    """MusicBrainz albums ("release groups", one per album instead of every pressing) and
    their covers from the Cover Art Archive. Free, no account."""

    name = "musicbrainz"
    label = "MusicBrainz"
    api = "https://musicbrainz.org/ws/2/release-group/"
    covers = "https://coverartarchive.org/release-group/"
    # MusicBrainz allows about one request per second and answers 503 "busy" otherwise
    # (also when it is just loaded, seen 2026-11-27); a short pause and one retry helps.
    spacing, retries, retry_pause = 1.1, 2, 1.5
    _lock = threading.Lock()
    _last = 0.0

    def get(self, url: str) -> Tuple[int, bytes]:
        if not url.startswith(self.api):
            return super().get(url)  # the cover archive has no such limit
        for attempt in range(self.retries + 1):
            with MusicBrainz._lock:
                wait = MusicBrainz._last + self.spacing - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                MusicBrainz._last = time.monotonic()
            status, body = super().get(url)
            if status != 503 or attempt == self.retries:
                if status == 503:
                    raise CoverError("MusicBrainz is busy right now, try again in a moment")
                return status, body
            time.sleep(self.retry_pause)
        raise AssertionError("unreachable")

    def search(self, album: str, artist: str, limit: int = SEARCH_LIMIT) -> List[Album]:
        terms = []
        if album.strip():
            terms.append(f"releasegroup:({lucene(album)})")
        if artist.strip():
            terms.append(f"artist:({lucene(artist)})")
        if not terms:
            raise ValueError("Enter an album or an artist.")
        query = urllib.parse.urlencode({"query": " AND ".join(terms), "fmt": "json",
                                        "limit": str(limit)})
        data = self.get_json(f"{self.api}?{query}")
        out = []
        for g in data.get("release-groups") or []:
            if not isinstance(g, dict) or not g.get("id"):
                continue
            credit = "".join(c.get("name", "") + c.get("joinphrase", "")
                             for c in g.get("artist-credit") or [] if isinstance(c, dict))
            kind = " · ".join([g.get("primary-type") or ""] + list(g.get("secondary-types") or []))
            out.append(Album(self.name, g["id"], g.get("title", ""), credit,
                             (g.get("first-release-date") or "")[:4], kind.strip(" ·"),
                             f"{self.covers}{g['id']}/front-250"))
        return out

    def cover_urls(self, ref: str) -> List[str]:
        if not re.fullmatch(r"[0-9a-f-]{36}", ref):
            raise ValueError("not a MusicBrainz id")
        return [f"{self.covers}{ref}/front-1200", f"{self.covers}{ref}/front-500"]


class ITunes(CoverSource):
    """Apple's iTunes Search API: albums as sold in the store (often remasters), with
    consistent high-resolution covers. Free, no account, about 20 searches a minute."""

    name = "itunes"
    label = "Apple Music"
    api = "https://itunes.apple.com/"

    @property
    def country(self) -> str:
        return (self.cfg.get("ITUNES_COUNTRY") or "US").strip().upper()

    def search(self, album: str, artist: str, limit: int = SEARCH_LIMIT) -> List[Album]:
        term = " ".join(f"{artist} {album}".split())
        if not term:
            raise ValueError("Enter an album or an artist.")
        query = urllib.parse.urlencode({"term": term, "media": "music", "entity": "album",
                                        "limit": str(limit), "country": self.country})
        data = self.get_json(f"{self.api}search?{query}")
        out = []
        for r in data.get("results") or []:
            if not isinstance(r, dict) or not r.get("collectionId"):
                continue
            tracks = r.get("trackCount")
            out.append(Album(self.name, str(r["collectionId"]), r.get("collectionName", ""),
                             r.get("artistName", ""), (r.get("releaseDate") or "")[:4],
                             f"{tracks} tracks" if tracks else "",
                             self.sized(r.get("artworkUrl100", ""), 250)))
        return out

    @staticmethod
    def sized(url: str, size: int) -> str:
        # The store's image URLs end in "100x100bb.jpg"; other sizes are made on request.
        # Not documented, but what Apple's own pages use.
        return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url)

    def cover_urls(self, ref: str) -> List[str]:
        if not ref.isdigit():
            raise ValueError("not an iTunes id")
        query = urllib.parse.urlencode({"id": ref, "entity": "album", "country": self.country})
        found = [r for r in self.get_json(f"{self.api}lookup?{query}").get("results") or []
                 if isinstance(r, dict) and r.get("artworkUrl100")]
        if not found:
            raise CoverError("Apple Music has no cover for this album")
        art = found[0]["artworkUrl100"]
        return [self.sized(art, 1200), self.sized(art, 600), art]


SOURCES = {cls.name: cls for cls in (MusicBrainz, ITunes)}


def configured(cfg: Dict[str, str], fetch: Fetch = http_get) -> Dict[str, CoverSource]:
    """The sources named in COVER_SOURCES, in that order (empty = all)."""
    names = [n.strip().lower() for n in cfg.get("COVER_SOURCES", "").split(",") if n.strip()]
    out: Dict[str, CoverSource] = {}
    for name in names or list(SOURCES):
        if name in SOURCES:
            out[name] = SOURCES[name](cfg, fetch)
        else:
            log.warning("COVER_SOURCES: unknown source %r (known: %s)", name,
                        ", ".join(SOURCES))
    return out
