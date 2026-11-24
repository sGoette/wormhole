import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from wormhole import config, feed


class FfmpegCommandTest(unittest.TestCase):
    def test_tone_is_paced_and_16bit_to_stdout(self):
        cmd = feed.ffmpeg_command("tone", 44100, 16)
        self.assertIn("-re", cmd)
        self.assertIn("sine=frequency=440:sample_rate=44100", cmd)
        self.assertEqual(cmd[-5:], ["-ar", "44100", "-f", "s16le", "-"])

    def test_stream_url_not_paced(self):
        cmd = feed.ffmpeg_command("http://mac:8765/live/tone/stream.mp3", 48000, 32)
        self.assertNotIn("-re", cmd)
        self.assertIn("s32le", cmd)

    def test_file_loops(self):
        cmd = feed.ffmpeg_command("/m/rec.flac", 44100, 16)
        self.assertEqual(cmd[cmd.index("-stream_loop") + 1], "-1")

    def test_logs_levels_but_no_progress(self):
        cmd = feed.ffmpeg_command("http://mac:8765/live/tone/stream.flac", 44100, 16)
        self.assertEqual(cmd[cmd.index("-loglevel") + 1], "level+info")
        self.assertIn("-nostats", cmd)

    def test_rejects_formats_owntone_refuses(self):
        with self.assertRaises(ValueError):
            feed.ffmpeg_command("tone", 22050, 16)
        with self.assertRaises(ValueError):
            feed.ffmpeg_command("tone", 44100, 24)


class AudioStreamTest(unittest.TestCase):
    def test_lossless_and_lossy(self):
        self.assertEqual(
            feed.parse_audio_stream("  Stream #0:0: Audio: flac, 48000 Hz, stereo, s32 (24 bit)"),
            {"codec": "flac", "rate": 48000, "bits": 24, "lossless": True})
        self.assertEqual(
            feed.parse_audio_stream("Stream #0:0: Audio: mp3, 44100 Hz, stereo, fltp, 320 kb/s"),
            {"codec": "mp3", "rate": 44100, "bits": None, "lossless": False})
        self.assertEqual(feed.parse_audio_stream(
            "Stream #0:0: Audio: pcm_s16le, 44100 Hz, 2 channels, s16, 1411 kb/s")["bits"], 16)
        self.assertTrue(feed.parse_audio_stream(
            "Stream #0:0: Audio: pcm_s24le, 96000 Hz, 2 channels, s32 (24 bit)")["lossless"])
        self.assertIsNone(feed.parse_audio_stream("Stream mapping:"))

    def test_drain_takes_the_input_and_keeps_errors(self):
        # what `ffmpeg -loglevel level+info` writes for a 24-bit FLAC stream that then fails
        log_lines = [
            "[info] Input #0, flac, from 'http://192.0.2.10:8145/stream.flac':",
            "[info]   Duration: N/A, start: 0.000000, bitrate: N/A",
            "[info]   Stream #0:0: Audio: flac, 48000 Hz, stereo, s32 (24 bit)",
            "[info] Stream mapping:",
            "[info] Output #0, s16le, to 'pipe:':",
            "[info]   Stream #0:0: Audio: pcm_s16le, 44100 Hz, stereo, s16, 1411 kb/s",
            "[flac @ 0x7f] [warning] invalid frame header",
            "[tcp @ 0x7f] [error] Connection reset by peer",
            "an unmarked line",
        ]
        f = feed.Feeder("tone", Path("/tmp/x"), 44100, 16)
        errors = feed.deque(maxlen=10)
        with self.assertLogs("wormhole.feed", "INFO"):
            f._drain(iter((line + "\n").encode() for line in log_lines), errors)
        self.assertEqual(f.input, {"codec": "flac", "rate": 48000, "bits": 24, "lossless": True})
        self.assertEqual(list(errors), ["[tcp @ 0x7f] Connection reset by peer",
                                        "an unmarked line"])


class FifoTest(unittest.TestCase):
    def test_creates_fifo_and_rejects_regular_file(self):
        with tempfile.TemporaryDirectory() as d:
            pipe = Path(d) / "sub" / "turntable"
            feed.ensure_fifo(pipe)
            self.assertTrue(pipe.is_fifo())
            feed.ensure_fifo(pipe)  # idempotent
            regular = Path(d) / "file"
            regular.write_text("x")
            with self.assertRaises(SystemExit):
                feed.ensure_fifo(regular)


def writer_cmd(script: str):
    """A stand-in for ffmpeg: a tiny Python process writing to stdout."""
    return [sys.executable, "-c", script]


class RelayTest(unittest.TestCase):
    """Feeder relays its child's stdout into the FIFO (no ffmpeg needed)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pipe = Path(tmp.name) / "turntable"
        os.mkfifo(self.pipe)
        self.events = []

    def start(self, script: str, reader: bool = True):
        f = feed.Feeder("tone", self.pipe, 44100, 16,
                        on_first_data=lambda: self.events.append(("first", f.bytes_written)))
        f.cmd = writer_cmd(script)
        f.MIN_BACKOFF = 0.05
        self.reader_fd = os.open(self.pipe, os.O_RDONLY | os.O_NONBLOCK) if reader else None
        thread = threading.Thread(target=f.run, daemon=True)
        thread.start()
        self.addCleanup(lambda: (f.stop(), thread.join(3)))
        return f, thread

    def read_all(self, until: float) -> bytes:
        data = b""
        end = time.time() + until
        while time.time() < end:
            try:
                data += os.read(self.reader_fd, 65536)
            except BlockingIOError:
                time.sleep(0.01)
        return data

    def test_relays_bytes_and_calls_first_data_once_before_writing(self):
        f, thread = self.start(
            "import sys, time\n"
            "for i in range(3):\n"
            "    sys.stdout.buffer.write(bytes([i]) * 1000); sys.stdout.flush(); time.sleep(0.05)\n"
            "time.sleep(5)")
        data = self.read_all(0.6)
        self.assertEqual(data, b"\x00" * 1000 + b"\x01" * 1000 + b"\x02" * 1000)
        self.assertEqual(self.events, [("first", 0)])  # before the first byte was written
        self.assertEqual(f.bytes_written, 3000)
        self.assertTrue(f.flowing())
        f.stop()
        thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_fifo_stays_open_across_restarts(self):
        f, _ = self.start("import sys; sys.stdout.buffer.write(b'x' * 100)")  # exits at once
        data = self.read_all(0.5)
        self.assertGreaterEqual(f.restarts, 2)
        self.assertGreaterEqual(len(data), 200)
        self.assertEqual(len(self.events), 1)  # first-data only once per feed
        # no EOF seen by the reader: the writer end was never closed in between
        self.assertIsNotNone(f._fd)

    def test_stop_while_waiting_for_a_reader(self):
        f, thread = self.start("import sys, time; sys.stdout.buffer.write(b'x'); "
                               "sys.stdout.flush(); time.sleep(5)", reader=False)
        time.sleep(0.4)
        self.assertEqual(f.bytes_written, 0)
        # OwnTone (pipe_autostart off) opens the pipe only after it was told to play
        self.assertEqual(self.events, [("first", 0)])
        f.stop()
        thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_stop_while_pipe_is_full(self):
        f, thread = self.start("import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)")
        time.sleep(0.4)  # nobody reads: the FIFO fills up and writes would block
        f.stop()
        thread.join(3)
        self.assertFalse(thread.is_alive())


class ConfigPathTest(unittest.TestCase):
    def test_relative_paths_resolve_against_root(self):
        self.assertEqual(config.path({"P": "a/b"}, "P"), config.ROOT / "a" / "b")
        self.assertEqual(config.path({"P": "/abs"}, "P"), Path("/abs"))

    def test_missing_setting_exits(self):
        with self.assertRaises(SystemExit):
            config.path({}, "P")

    def test_shipped_configs_have_feed_settings(self):
        for name in ("config/local.env", "server/wormhole.env.example"):
            cfg = config.parse_env_file(config.ROOT / name)
            for key in ("PCM_PIPE", "PIPE_SAMPLE_RATE", "PIPE_BITS", "OWNTONE_URL"):
                self.assertIn(key, cfg, f"{key} missing in {name}")
            feed.ffmpeg_command("tone", int(cfg["PIPE_SAMPLE_RATE"]), int(cfg["PIPE_BITS"]))


if __name__ == "__main__":
    unittest.main()
