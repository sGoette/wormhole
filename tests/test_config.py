import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wormhole import config


class ConfigTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        fd, name = tempfile.mkstemp(suffix=".env")
        os.close(fd)
        path = Path(name)
        path.write_text(text)
        self.addCleanup(path.unlink)
        return path

    def test_parses_comments_blanks_and_quotes(self):
        path = self.write('# c\n\nA=1\nB="two words"\nC=\nD=x=y\n')
        expected = {"A": "1", "B": "two words", "C": "", "D": "x=y"}
        self.assertEqual(config.parse_env_file(path), expected)

    def test_environment_overrides_file(self):
        path = self.write("STREAM_PORT=8765\n")
        with mock.patch.dict(os.environ, {"STREAM_PORT": "9999"}):
            self.assertEqual(config.load(str(path))["STREAM_PORT"], "9999")

    def test_missing_file_gives_empty_config(self):
        self.assertEqual(config.load("/nonexistent/x.env"), {})

    def test_optional_path(self):
        cfg = {"A": "rel/x.jpg", "B": "  ", "C": "/abs/y.jpg"}
        self.assertEqual(config.optional_path(cfg, "A"), config.ROOT / "rel" / "x.jpg")
        self.assertIsNone(config.optional_path(cfg, "B"))
        self.assertIsNone(config.optional_path(cfg, "missing"))
        self.assertEqual(config.optional_path(cfg, "C"), Path("/abs/y.jpg"))

    def test_shipped_env_files_parse(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("config/local.env", "server/wormhole.env.example"):
            self.assertIn("WEB_PORT", config.parse_env_file(root / name), name)

    def test_mac_and_server_configs_have_the_same_keys(self):
        root = Path(__file__).resolve().parents[1]
        mac = set(config.parse_env_file(root / "config/local.env"))
        server = set(config.parse_env_file(root / "server/wormhole.env.example"))
        mac_only = {"STREAM_PORT"}  # fake turntable, Mac only
        self.assertEqual(mac - mac_only, server)


if __name__ == "__main__":
    unittest.main()
