"""Record library: the user's albums with their covers, in RECORDS_DIR (SQLite + covers/).

RECORDS_DIR lies outside the checkout (server: /var/lib/wormhole/records), so reinstalling or
updating the app never touches it. Covers are files, not database blobs: OwnTone reads them
by path for the "now playing" artwork, and a backup is a plain copy of the folder.
"""

import os
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List, Optional

from .covers import COVER_BYTES_MAX, image_kind

TEXT_MAX = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',      -- where it was found (covers.SOURCES), '' = by hand
    source_ref TEXT NOT NULL DEFAULT '',  -- that source's id for it
    cover TEXT NOT NULL DEFAULT '',       -- file name in covers/, '' = none
    added REAL NOT NULL
);
"""
VERSION = 1  # PRAGMA user_version: bump with a migration step in _connect()


@dataclass
class Record:
    id: int
    title: str
    artist: str
    year: str
    source: str
    source_ref: str
    cover: str
    added: float

    def to_json(self) -> dict:
        d = asdict(self)
        # The file name changes with every new cover, so browsers never show an old one.
        d["cover_url"] = f"/covers/{self.cover}" if self.cover else ""
        return d


def clean(value: Any, field: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"{field} must be text")
    value = " ".join(str(value).split())
    if required and not value:
        raise ValueError(f"Enter the {field}.")
    if len(value) > TEXT_MAX:
        raise ValueError(f"The {field} can have at most {TEXT_MAX} characters.")
    if not value.isprintable():
        raise ValueError(f"The {field} contains characters that can't be shown.")
    return value


class Records:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "records.db"
        self.covers = root / "covers"
        self.lock = threading.Lock()  # the web server answers requests in threads

    def _connect(self) -> sqlite3.Connection:
        self.covers.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db, timeout=5)
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        if con.execute("PRAGMA user_version").fetchone()[0] < VERSION:
            con.execute(f"PRAGMA user_version = {VERSION}")
        return con

    def all(self) -> List[Record]:
        with self.lock, closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM records ORDER BY artist COLLATE NOCASE, "
                               "year, title COLLATE NOCASE").fetchall()
        return [Record(**dict(r)) for r in rows]

    def get(self, record_id: Any) -> Record:
        with self.lock, closing(self._connect()) as con:
            row = con.execute("SELECT * FROM records WHERE id = ?",
                              (self._id(record_id),)).fetchone()
        if row is None:
            raise ValueError("This record is no longer in the library.")
        return Record(**dict(row))

    def find(self, source: str, source_ref: str) -> Optional[Record]:
        if not source or not source_ref:
            return None
        with self.lock, closing(self._connect()) as con:
            row = con.execute("SELECT * FROM records WHERE source = ? AND source_ref = ?",
                              (source, source_ref)).fetchone()
        return Record(**dict(row)) if row else None

    def add(self, title: Any, artist: Any = "", year: Any = "", source: str = "",
            source_ref: str = "", cover: Optional[bytes] = None) -> Record:
        values = (clean(title, "album title", required=True), clean(artist, "artist"),
                  clean(year, "year"), source, source_ref, time.time())
        if cover is not None:
            self._check_image(cover)
        with self.lock, closing(self._connect()) as con, con:
            cur = con.execute("INSERT INTO records (title, artist, year, source, source_ref, "
                              "added) VALUES (?, ?, ?, ?, ?, ?)", values)
            record_id = cur.lastrowid
        return self.set_cover(record_id, cover) if cover is not None else self.get(record_id)

    def update(self, record_id: Any, title: Any = None, artist: Any = None,
               year: Any = None) -> Record:
        record = self.get(record_id)
        new = (clean(title, "album title", required=True) if title is not None else record.title,
               clean(artist, "artist") if artist is not None else record.artist,
               clean(year, "year") if year is not None else record.year)
        with self.lock, closing(self._connect()) as con, con:
            con.execute("UPDATE records SET title = ?, artist = ?, year = ? WHERE id = ?",
                        new + (record.id,))
        return self.get(record.id)

    def set_cover(self, record_id: Any, data: bytes) -> Record:
        record = self.get(record_id)
        kind = self._check_image(data)
        name = f"{record.id}-{int(time.time() * 1000):x}.{kind}"
        self.covers.mkdir(parents=True, exist_ok=True)
        # Temporary file + rename: OwnTone or a browser never reads half a picture.
        fd, tmp = tempfile.mkstemp(dir=self.covers, prefix=".cover-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o644)  # OwnTone runs as another user and reads it by path
            os.replace(tmp, self.covers / name)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        with self.lock, closing(self._connect()) as con, con:
            con.execute("UPDATE records SET cover = ? WHERE id = ?", (name, record.id))
        self._remove_cover(record.cover)
        return self.get(record.id)

    def delete(self, record_id: Any) -> None:
        record = self.get(record_id)
        with self.lock, closing(self._connect()) as con, con:
            con.execute("DELETE FROM records WHERE id = ?", (record.id,))
        self._remove_cover(record.cover)

    def cover_path(self, record: Record) -> Optional[Path]:
        return self.covers / record.cover if record.cover else None

    def _remove_cover(self, name: str) -> None:
        if name:
            try:
                (self.covers / name).unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _id(record_id: Any) -> int:
        try:
            return int(record_id)
        except (TypeError, ValueError):
            raise ValueError(f"not a record id: {record_id!r}") from None

    @staticmethod
    def _check_image(data: bytes) -> str:
        kind = image_kind(data)
        if kind is None:
            raise ValueError("The cover must be a JPEG or PNG picture.")
        if len(data) > COVER_BYTES_MAX:
            raise ValueError(f"The cover is too big ({len(data) // 1000} kB, at most "
                             f"{COVER_BYTES_MAX // 1000} kB).")
        return kind
