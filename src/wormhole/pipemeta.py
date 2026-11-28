"""Mid-stream "now playing" updates through OwnTone's metadata pipe (<PCM_PIPE>.metadata).

OwnTone opens `<pipe>.metadata` whenever it starts playing the pipe and reads Shairport Sync's
XML items from it (inputs/pipe.c, 29.4): `minm` title, `asar` artist, `asal` album, `PICT`
picture (JPEG or PNG). What arrives there replaces the playing queue item's info and goes to
the speakers right away. That's the only way to change it without restarting playback: the
JSON API's queue item update only writes OwnTone's database.
"""

import base64
import errno
import logging
import os
import select
import stat
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

# OwnTone drops pictures over 1 MiB and empties its buffer when more than 1 MiB of item text
# is pending (pipe.c, PIPE_PICTURE_SIZE_MAX / PIPE_METADATA_BUFLEN_MAX). Base64 adds a third,
# so a picture must stay well below 768 KiB.
PICTURE_MAX = 700_000


def item(code: str, data: bytes, kind: str = "core") -> bytes:
    """One Shairport Sync metadata item. OwnTone only looks at the code (and wants a type)."""
    return (f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>"
            f"<length>{len(data)}</length>\n<data encoding=\"base64\">\n"
            f"{base64.b64encode(data).decode()}</data></item>\n").encode()


def items(title: str, artist: str, picture: Optional[bytes] = None) -> bytes:
    """Items for one update. Empty values are left out: OwnTone ignores items without data,
    so a field can be replaced but never cleared."""
    out: List[bytes] = []
    if title:
        out.append(item("minm", title.encode()))
    if artist:
        out.append(item("asar", artist.encode()))
    if picture:
        if len(picture) > PICTURE_MAX:
            raise ValueError(f"picture too big for OwnTone ({len(picture)} bytes, "
                             f"at most {PICTURE_MAX})")
        out.append(item("PICT", picture, kind="ssnc"))
    return b"".join(out)


class MetadataPipe:
    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout

    @classmethod
    def for_pcm_pipe(cls, pcm_pipe: Path) -> "MetadataPipe":
        return cls(pcm_pipe.with_name(pcm_pipe.name + ".metadata"))

    def ensure(self) -> None:
        """Create the FIFO if it's missing. It must exist before playback starts: OwnTone
        looks for it only then. Readable by everyone, so OwnTone (another user) can open it
        without install.sh changing its group."""
        if self.path.exists():
            if not stat.S_ISFIFO(self.path.stat().st_mode):
                raise OSError(f"{self.path} exists but is not a named pipe")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(self.path)
        os.chmod(self.path, 0o644)  # mkfifo's mode is cut by the umask
        log.info("created metadata pipe %s", self.path)

    def send(self, title: str, artist: str, picture: Optional[bytes] = None) -> bool:
        """Write one update. False when OwnTone isn't reading (not playing the pipe): it gets
        the info from the queue item when playback starts (OwnTone.play_pipe)."""
        data = items(title, artist, picture)
        if not data:
            return False
        try:
            # Non-blocking: opening a FIFO nobody reads would wait forever.
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.ENXIO, errno.ENOENT):
                return False  # ENXIO: no reader. ENOENT: never created (ensure() failed).
            raise
        try:
            self._write_all(fd, data)
        finally:
            # Closing gives OwnTone an end of file; it reopens the pipe and waits for more.
            os.close(fd)
        log.info("now playing sent to OwnTone: %s / %s%s", title, artist,
                 " + picture" if picture else "")
        return True

    def _write_all(self, fd: int, data: bytes) -> None:
        """A picture is bigger than the pipe buffer (64 KiB): write as OwnTone reads, with a
        deadline so a stuck OwnTone can't hold up the web request for ever."""
        deadline = time.monotonic() + self.timeout
        view = memoryview(data)
        while view:
            left = deadline - time.monotonic()
            if left <= 0:
                raise OSError(f"OwnTone stopped reading {self.path}")
            _r, writable, _x = select.select([], [fd], [], left)
            if not writable:
                continue
            try:
                n = os.write(fd, view)
            except BlockingIOError:
                continue
            view = view[n:]
