"""Feed raw PCM into OwnTone's named pipe: input (tone, file or stream URL) -> ffmpeg -> FIFO.

ffmpeg writes PCM to stdout and this module relays it into the FIFO. That way we know when the
first audio is actually there (`on_first_data`, used to start OwnTone exactly then) and how
much is flowing, and the FIFO stays open while ffmpeg restarts after a dropout (OwnTone
underruns and resumes instead of seeing end-of-file).

Why start OwnTone only on the first data: OwnTone's start buffer is 2250 ms
(general.start_buffer_ms). A FLAC live stream takes ~3 s until ffmpeg emits PCM, so starting
OwnTone earlier left the HomePods silent while OwnTone itself played (2026-11-13).

PCM is little-endian (s16le / s32le): OwnTone reads host byte order, and both the Mac (arm64)
and the server (amd64) are little-endian.

What arrives is read from ffmpeg's own log (`-loglevel level+info`): the input's audio stream
line ("Audio: flac, 48000 Hz, stereo, s32 (24 bit)") gives codec, rate and bit depth, so the
web UI can show whether the turntable really sends lossless audio. No second connection to
the live stream (an ffprobe) is needed.
"""

import errno
import logging
import os
import re
import select
import signal
import stat
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Dict, IO, List, Optional

from . import config

log = logging.getLogger(__name__)

TONE = "tone"
VALID_RATES = (44100, 48000, 88200, 96000)  # what OwnTone's pipe input accepts
VALID_BITS = (16, 32)
CHUNK = 16384  # ~93 ms of 44.1 kHz s16 stereo
READER_CLOSED = "the reader closed the pipe"
LOSSLESS_CODECS = ("flac", "alac", "wavpack", "ape", "tta", "mlp", "truehd")  # and pcm_*
# "[tcp @ 0x7f] [error] Connection refused": the level that -loglevel level+info adds
LOG_LEVEL = re.compile(r"\[(panic|fatal|error|warning|info|verbose|debug|trace)\] ?")
AUDIO_STREAM = re.compile(r"Stream #\d+:\d+\S*: Audio: ([\w-]+)[^,]*, (\d+) Hz"
                          r"(?:, [^,]+)?(?:, (\w+)(?: \((\d+) bit\))?)?")


def parse_audio_stream(line: str) -> Optional[Dict]:
    """Codec, sample rate and bit depth from an ffmpeg "Stream #0:0: Audio: ..." line."""
    m = AUDIO_STREAM.search(line)
    if not m:
        return None
    codec, rate, fmt, bits = m.group(1), int(m.group(2)), m.group(3) or "", m.group(4)
    if bits is None:  # "s16" / "s32p" carry it; "fltp" (lossy decoders) does not
        sized = re.match(r"[su](\d+)", fmt)
        bits = sized.group(1) if sized else None
    return {"codec": codec, "rate": rate, "bits": int(bits) if bits else None,
            "lossless": codec in LOSSLESS_CODECS or codec.startswith("pcm_")}


def ffmpeg_command(source: str, rate: int, bits: int) -> List[str]:
    """ffmpeg that writes raw PCM for OwnTone's pipe to stdout."""
    if rate not in VALID_RATES:
        raise ValueError(f"sample rate {rate} not accepted by OwnTone ({VALID_RATES})")
    if bits not in VALID_BITS:
        raise ValueError(f"bits {bits} not accepted by OwnTone ({VALID_BITS})")
    # level+info: errors stay recognisable ("[error]"), and the input's format is logged
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "level+info", "-nostats", "-nostdin"]
    if source == TONE:
        cmd += ["-re", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}"]
    elif "://" in source:
        cmd += ["-i", source]  # a live stream paces itself
    else:
        cmd += ["-re", "-stream_loop", "-1", "-i", source]
    pcm = "s16le" if bits == 16 else "s32le"
    cmd += ["-map", "0:a:0", "-ac", "2", "-ar", str(rate), "-f", pcm, "-"]
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

    def __init__(self, source: str, pipe: Path, rate: int, bits: int,
                 on_first_data: Optional[Callable[[], None]] = None):
        self.cmd = ffmpeg_command(source, rate, bits)
        self.source, self.pipe = source, pipe
        self.on_first_data = on_first_data
        self.proc: Optional[subprocess.Popen] = None
        self.stopping = False
        self._wake = threading.Event()
        self._fd: Optional[int] = None
        # Status, read by the web UI.
        self.started_at: Optional[float] = None
        self.restarts = 0
        self.last_error = ""
        self.bytes_written = 0
        self.last_data_at = 0.0
        self.input: Optional[Dict] = None  # parse_audio_stream() of what ffmpeg decodes

    def install_signal_handlers(self) -> None:
        """Ctrl+C / SIGTERM stop the feed cleanly. Main thread only."""
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

    def stop(self, *_args) -> None:
        self.stopping = True
        self._wake.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def flowing(self, within: float = 2.0) -> bool:
        return time.time() - self.last_data_at < within

    # -- main loop
    def run(self) -> int:
        ensure_fifo(self.pipe)
        self.started_at = time.time()
        backoff = self.MIN_BACKOFF
        first_data_pending = self.on_first_data is not None
        try:
            while not self.stopping:
                log.info("feeding %s -> %s", self.source, self.pipe)
                log.debug("ffmpeg: %s", " ".join(self.cmd))
                started = time.monotonic()
                self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE)
                if self.stopping:  # stop() raced with Popen and saw no process yet
                    self.proc.terminate()
                errors: deque = deque(maxlen=10)
                drain = threading.Thread(target=self._drain, args=(self.proc.stderr, errors),
                                         daemon=True)
                drain.start()
                reason, first_data_pending = self._relay(self.proc.stdout, first_data_pending)
                self._end_process()
                drain.join(timeout=1)
                if self.stopping:
                    break
                ran = time.monotonic() - started
                detail = " | ".join(errors) or reason
                self.restarts += 1
                if reason == READER_CLOSED:
                    self.last_error = "OwnTone closed the pipe (paused or restarted)"
                    log.info("%s after %.0fs; reconnecting the stream", self.last_error, ran)
                else:
                    self.last_error = f"ffmpeg exited with {self.proc.returncode}: {detail}"
                    log.warning("ffmpeg exited with %s after %.0fs: %s",
                                self.proc.returncode, ran, detail)
                # OwnTone closing the pipe (pause, restart) is no fault of the stream: come
                # back at once, so the restarted player gets audio without a growing wait.
                healthy = ran > self.HEALTHY_AFTER or reason == READER_CLOSED
                backoff = self.MIN_BACKOFF if healthy else min(backoff * 2, self.MAX_BACKOFF)
                log.info("restarting in %.0fs", backoff)
                self._wake.wait(backoff)  # returns early on stop()
        finally:
            self._close_fifo()
            log.info("feed stopped")
        return 0

    def _relay(self, stdout: IO[bytes], first_data_pending: bool):
        """Copy ffmpeg's stdout into the FIFO. Returns (reason it ended, first_data_pending)."""
        while not self.stopping:
            chunk = stdout.read1(CHUNK)
            if not chunk:
                return "ffmpeg ended its output", first_data_pending
            # Start OwnTone first: its own queue item has wormhole's "now playing" info, and
            # OwnTone's autostart then finds the pipe already playing. (With pipe_autostart
            # off there is no reader until playback starts, so waiting first would hang.)
            if first_data_pending:
                first_data_pending = False
                self._first_data()
            if self._fd is None and not self._open_fifo():
                return "stopped while waiting for a pipe reader", first_data_pending
            if not self._write(chunk):
                return READER_CLOSED, first_data_pending
        return "stopped", first_data_pending

    def _first_data(self) -> None:
        log.info("first audio data; starting playback")
        try:
            self.on_first_data()
        except Exception as exc:  # noqa: BLE001 - report any failure, keep feeding
            self.last_error = f"could not start playback: {exc}"
            log.warning("%s", self.last_error)

    # -- FIFO handling (non-blocking, so stop() is never stuck behind a full pipe)
    def _open_fifo(self) -> bool:
        waiting_logged = False
        while not self.stopping:
            try:
                self._fd = os.open(self.pipe, os.O_WRONLY | os.O_NONBLOCK)
                return True
            except OSError as exc:
                if exc.errno != errno.ENXIO:  # ENXIO = no reader yet
                    raise
            if not waiting_logged:
                log.warning("waiting for a reader on %s (OwnTone not playing?)", self.pipe)
                waiting_logged = True
            self._wake.wait(0.5)
        return False

    def _write(self, chunk: bytes) -> bool:
        view = memoryview(chunk)
        while view and not self.stopping:
            try:
                written = os.write(self._fd, view)
                view = view[written:]
            except BlockingIOError:
                select.select([], [self._fd], [], 0.5)
            except BrokenPipeError:
                self._close_fifo()
                return False
        self.bytes_written += len(chunk)
        self.last_data_at = time.time()
        return True

    def _close_fifo(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _end_process(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def _drain(self, stream: IO[bytes], into: deque) -> None:
        """ffmpeg's log: errors go to `into`, the input's audio format to self.input."""
        in_input = False
        for line in stream:
            text = line.decode("utf-8", "replace").strip()
            m = LOG_LEVEL.search(text)
            level = m.group(1) if m else "error"
            msg = f"{text[:m.start()]}{text[m.end():]}".strip() if m else text
            if not msg or level == "warning":
                continue
            if level in ("panic", "fatal", "error"):
                into.append(msg)
            elif msg.startswith("Input #"):
                in_input = True
            elif msg.startswith(("Output #", "Stream mapping")):
                in_input = False  # the output's "Stream ... Audio: pcm_s16le" is ours
            elif in_input:
                found = parse_audio_stream(msg)
                if found and found != self.input:
                    self.input = found
                    bits = f", {found['bits']} bit" if found["bits"] else ""
                    log.info("input: %s %s Hz%s, %s", found["codec"], found["rate"], bits,
                             "lossless" if found["lossless"] else "lossy")


class FeedControl:
    """Runs one Feeder in a background thread (web UI and renderer share it)."""

    def __init__(self, cfg: Dict[str, str], factory: Callable[..., Feeder] = Feeder):
        self.cfg, self.factory = cfg, factory
        self.lock = threading.Lock()
        self.feeder: Optional[Feeder] = None
        self.thread: Optional[threading.Thread] = None
        self.source = ""
        self.origin = ""  # who started it: "web" or "upnp"

    def resolve(self, source: str) -> str:
        """Allow 'tone', http(s) URLs and existing files; relative paths are from ROOT."""
        source = source.strip()
        if source == TONE or source.startswith(("http://", "https://")):
            return source
        path = Path(source).expanduser()
        path = path if path.is_absolute() else config.ROOT / path
        if not path.is_file():
            raise ValueError(f"not 'tone', an http(s) URL or an existing file: {source}")
        return str(path)

    def start(self, source: str, origin: str = "web",
              on_first_data: Optional[Callable[[], None]] = None) -> None:
        resolved = self.resolve(source)
        self.stop()
        feeder = self.factory(
            source=resolved,
            pipe=config.path(self.cfg, "PCM_PIPE"),
            rate=int(self.cfg.get("PIPE_SAMPLE_RATE", "44100")),
            bits=int(self.cfg.get("PIPE_BITS", "16")),
            on_first_data=on_first_data,
        )
        thread = threading.Thread(target=feeder.run, name="feed", daemon=True)
        with self.lock:
            self.feeder, self.thread = feeder, thread
            self.source, self.origin = source.strip(), origin
        thread.start()

    def owned_by(self, origin: str) -> bool:
        """True while a feed started by `origin` is running."""
        state = self.state()
        return state["running"] and state["origin"] == origin

    def stop(self, origin: Optional[str] = None) -> None:
        """Stop the feed; with `origin`, only if that origin started it."""
        with self.lock:
            feeder, thread = self.feeder, self.thread
            if origin is not None and self.origin != origin:
                return
        if feeder is not None:
            feeder.stop()
        if thread is not None:
            thread.join(timeout=5)

    def state(self) -> dict:
        with self.lock:
            feeder, thread, source, origin = self.feeder, self.thread, self.source, self.origin
        running = bool(thread and thread.is_alive() and feeder and not feeder.stopping)
        return {
            "running": running,
            "source": source,
            "origin": origin,
            "seconds": int(time.time() - feeder.started_at)
            if running and feeder and feeder.started_at else 0,
            "restarts": feeder.restarts if feeder else 0,
            "last_error": feeder.last_error if feeder else "",
            "bytes": feeder.bytes_written if feeder else 0,
            "flowing": bool(running and feeder.flowing()),
            "input": feeder.input if running and feeder else None,
        }
