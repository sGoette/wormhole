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
        self.assertEqual(config.parse_env_file(path), {"A": "1", "B": "two words", "C": "", "D": "x=y"})

    def test_environment_overrides_file(self):
        path = self.write("STREAM_PORT=8765\n")
        with mock.patch.dict(os.environ, {"STREAM_PORT": "9999"}):
            self.assertEqual(config.load(str(path))["STREAM_PORT"], "9999")

    def test_missing_file_gives_empty_config(self):
        self.assertEqual(config.load("/nonexistent/x.env"), {})

    def test_shipped_env_files_parse(self):
        root = Path(__file__).resolve().parents[1] / "config"
        for name in ("local.env", "nas.env.example"):
            self.assertIn("STREAM_PORT", config.parse_env_file(root / name), name)


if __name__ == "__main__":
    unittest.main()
