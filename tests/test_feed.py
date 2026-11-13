import os
import tempfile
import unittest
from pathlib import Path

from wormhole import config, feed


class FfmpegCommandTest(unittest.TestCase):
    PIPE = Path("/x/turntable")

    def test_tone_is_paced_and_16bit(self):
        cmd = feed.ffmpeg_command("tone", self.PIPE, 44100, 16)
        self.assertIn("-re", cmd)
        self.assertIn("sine=frequency=440:sample_rate=44100", cmd)
        self.assertEqual(cmd[-6:], ["-ar", "44100", "-f", "s16le", "-y", "/x/turntable"])

    def test_stream_url_not_paced(self):
        cmd = feed.ffmpeg_command("http://mac:8765/live/tone/stream.mp3", self.PIPE, 48000, 32)
        self.assertNotIn("-re", cmd)
        self.assertIn("s32le", cmd)

    def test_file_loops(self):
        cmd = feed.ffmpeg_command("/m/rec.flac", self.PIPE, 44100, 16)
        self.assertEqual(cmd[cmd.index("-stream_loop") + 1], "-1")

    def test_rejects_formats_owntone_refuses(self):
        with self.assertRaises(ValueError):
            feed.ffmpeg_command("tone", self.PIPE, 22050, 16)
        with self.assertRaises(ValueError):
            feed.ffmpeg_command("tone", self.PIPE, 44100, 24)


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


class ConfigPathTest(unittest.TestCase):
    def test_relative_paths_resolve_against_root(self):
        self.assertEqual(config.path({"P": "a/b"}, "P"), config.ROOT / "a" / "b")
        self.assertEqual(config.path({"P": "/abs"}, "P"), Path("/abs"))

    def test_missing_setting_exits(self):
        with self.assertRaises(SystemExit):
            config.path({}, "P")

    def test_shipped_configs_have_feed_settings(self):
        for name in ("local.env", "nas.env.example"):
            cfg = config.parse_env_file(config.ROOT / "config" / name)
            for key in ("PCM_PIPE", "PIPE_SAMPLE_RATE", "PIPE_BITS", "OWNTONE_URL"):
                self.assertIn(key, cfg, f"{key} missing in {name}")
            feed.ffmpeg_command("tone", Path("/p"), int(cfg["PIPE_SAMPLE_RATE"]),
                                int(cfg["PIPE_BITS"]))


if __name__ == "__main__":
    unittest.main()
