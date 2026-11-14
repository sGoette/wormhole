"""Settings file written from the web UI."""

import json
import tempfile
import unittest
from pathlib import Path

from wormhole.settings import Settings, clean_renderer_name


class SettingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sub" / "settings.json"

    def test_missing_file_is_empty_and_set_creates_it(self):
        s = Settings(self.path)
        self.assertIsNone(s.get("renderer_name"))
        s.set("renderer_name", "Kitchen")
        s.set("other", 1)
        self.assertEqual(json.loads(self.path.read_text()), {"renderer_name": "Kitchen",
                                                             "other": 1})
        self.assertEqual(Settings(self.path).get("renderer_name"), "Kitchen")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["settings.json"])

    def test_broken_file_is_ignored(self):
        self.path.parent.mkdir()
        self.path.write_text("{not json")
        self.assertEqual(Settings(self.path).get("renderer_name", "x"), "x")
        self.path.write_text("[1, 2]")
        self.assertEqual(Settings(self.path).values, {})

    def test_without_path_nothing_is_saved(self):
        s = Settings(None)
        with self.assertRaises(ValueError):
            s.set("renderer_name", "Kitchen")
        self.assertEqual(s.values, {})

    def test_unwritable_directory(self):
        self.path.parent.mkdir()
        self.path.parent.chmod(0o500)
        self.addCleanup(self.path.parent.chmod, 0o700)
        s = Settings(self.path)
        with self.assertRaises(ValueError) as ctx:
            s.set("renderer_name", "Kitchen")
        self.assertIn("cannot save", str(ctx.exception))
        self.assertEqual(s.values, {})


class RendererNameTest(unittest.TestCase):
    def test_cleaned(self):
        self.assertEqual(clean_renderer_name("  Living   Room \n"), "Living Room")
        self.assertEqual(clean_renderer_name("Küche (HomePods) ♫"), "Küche (HomePods) ♫")

    def test_refused(self):
        for bad in ["", "  ", "x" * 65, "a\x00b", None, 3]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                clean_renderer_name(bad)


if __name__ == "__main__":
    unittest.main()
