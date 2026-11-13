"""Feed raw PCM into OwnTone's named pipe: input (tone, file or stream URL) -> ffmpeg -> FIFO.

OwnTone (pipe_autostart) starts playing when data arrives. ffmpeg is restarted with backoff
when it exits, e.g. when the source stream drops or OwnTone closes the pipe.
PCM is little-endian (s16le / s32le): OwnTone reads host byte order, and both the Mac (arm64)
and the NAS (amd64) are little-endian.
"""

import logging
import os
import signal
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

TONE = "tone"
VALID_RATES = (44100, 48000, 88200, 96000)  # what OwnTone's pipe input accepts
VALID_BITS = (16, 32)


def ffmpeg_command(source: str, pipe: Path, rate: int, bits: int) -> List[str]:
    if rate not in VALID_RATES:
        raise ValueError(f"sample rate {rate} not accepted by OwnTone ({VALID_RATES})")
    if bits not in VALID_BITS:
        raise ValueError(f"bits {bits} not accepted by OwnTone ({VALID_BITS})")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if source == TONE:
        cmd += ["-re", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}"]
    elif "://" in source:
        cmd += ["-i", source]  # a live stream paces itself
    else:
        cmd += ["-re", "-stream_loop", "-1", "-i", source]
    pcm = "s16le" if bits == 16 else "s32le"
    cmd += ["-map", "0:a:0", "-ac", "2", "-ar", str(rate), "-f", pcm, "-y", str(pipe)]
    return cmd


def ensure_fifo(pipe: Path) -> None:
    if pipe.exists():
        if not stat.S_ISFIFO(pipe.stat().st_mode):
            raise SystemExit(f"{pipe} exists but is not a named pipe")
        return
    pipe.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(pipe)
    log.info("created named pipe %s", pipe)


class Feeder:
    MIN_BACKOFF, MAX_BACKOFF, HEALTHY_AFTER = 1.0, 15.0, 30.0

    def __init__(self, source: str, pipe: Path, rate: int, bits: int):
        self.cmd = ffmpeg_command(source, pipe, rate, bits)
        self.source, self.pipe = source, pipe
        self.proc: Optional[subprocess.Popen] = None
        self.stopping = False
        self._wake = threading.Event()

    def stop(self, *_args) -> None:
        self.stopping = True
        self._wake.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def run(self) -> int:
        ensure_fifo(self.pipe)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        backoff = self.MIN_BACKOFF
        while not self.stopping:
            log.info("feeding %s -> %s", self.source, self.pipe)
            log.debug("ffmpeg: %s", " ".join(self.cmd))
            started = time.monotonic()
            # Opening a FIFO for writing blocks until a reader (OwnTone) has it open.
            self.proc = subprocess.Popen(self.cmd, stderr=subprocess.PIPE)
            _, err = self.proc.communicate()
            if self.stopping:
                break
            ran = time.monotonic() - started
            detail = err.decode("utf-8", "replace").strip() or "no error output"
            log.warning("ffmpeg exited with %s after %.0fs: %s", self.proc.returncode, ran, detail)
            if ran > self.HEALTHY_AFTER:
                backoff = self.MIN_BACKOFF
            else:
                backoff = min(backoff * 2, self.MAX_BACKOFF)
            log.info("restarting in %.0fs", backoff)
            self._wake.wait(backoff)  # returns early on stop()
        log.info("feed stopped")
        return 0
