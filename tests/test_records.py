"""Record library in a temporary RECORDS_DIR: database rows and cover files."""

import stat
import tempfile
import unittest
from pathlib import Path

from wormhole.covers import COVER_BYTES_MAX
from wormhole.records import Records

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 1000
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 1000


class RecordsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "records"
        self.lib = Records(self.root)

    def test_empty_library_creates_its_folder(self):
        self.assertEqual(self.lib.all(), [])
        self.assertTrue((self.root / "records.db").is_file())
        self.assertTrue((self.root / "covers").is_dir())

    def test_add_and_list_sorted_by_artist(self):
        self.lib.add("Kind of Blue", "Miles Davis", "1959")
        self.lib.add("Blue Train", "john coltrane", 1957, source="musicbrainz", source_ref="x")
        self.lib.add("Giant Steps", "John Coltrane", "1960")
        self.assertEqual([r.title for r in self.lib.all()],
                         ["Blue Train", "Giant Steps", "Kind of Blue"])
        blue = self.lib.all()[0]
        self.assertEqual((blue.year, blue.source, blue.source_ref, blue.cover),
                         ("1957", "musicbrainz", "x", ""))
        self.assertEqual(blue.to_json()["cover_url"], "")

    def test_survives_a_new_instance(self):
        added = self.lib.add("Blue Train", "John Coltrane", cover=JPEG)
        again = Records(self.root).get(added.id)
        self.assertEqual(again, added)
        self.assertEqual(Records(self.root).cover_path(again).read_bytes(), JPEG)

    def test_text_is_cleaned_and_checked(self):
        r = self.lib.add("  Blue \n Train ", " John  Coltrane ")
        self.assertEqual((r.title, r.artist), ("Blue Train", "John Coltrane"))
        for bad in ("", "   ", None, "x" * 201, "bell\x07"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.lib.add(bad)
        with self.assertRaises(ValueError):
            self.lib.add("ok", artist=["not", "text"])

    def test_cover_file_and_url(self):
        r = self.lib.add("Blue Train", cover=JPEG)
        path = self.lib.cover_path(r)
        self.assertEqual(path.read_bytes(), JPEG)
        self.assertTrue(path.name.startswith(f"{r.id}-") and path.suffix == ".jpg")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)  # OwnTone reads it
        self.assertEqual(r.to_json()["cover_url"], f"/covers/{path.name}")

    def test_new_cover_replaces_the_old_file(self):
        r = self.lib.add("Blue Train", cover=JPEG)
        old = self.lib.cover_path(r)
        r = self.lib.set_cover(r.id, PNG)
        self.assertFalse(old.exists())
        self.assertEqual(self.lib.cover_path(r).suffix, ".png")
        self.assertEqual(len(list((self.root / "covers").iterdir())), 1)

    def test_bad_covers_refused(self):
        r = self.lib.add("Blue Train")
        with self.assertRaisesRegex(ValueError, "JPEG or PNG"):
            self.lib.set_cover(r.id, b"GIF89a....")
        with self.assertRaisesRegex(ValueError, "too big"):
            self.lib.set_cover(r.id, b"\xff\xd8\xff" + b"x" * COVER_BYTES_MAX)
        with self.assertRaises(ValueError):
            self.lib.add("Other", cover=b"nope")
        self.assertEqual([x.title for x in self.lib.all()], ["Blue Train"])

    def test_update(self):
        r = self.lib.add("Blue Train", "Coltrane", "1957")
        r = self.lib.update(r.id, artist="John Coltrane")
        self.assertEqual((r.title, r.artist, r.year), ("Blue Train", "John Coltrane", "1957"))
        with self.assertRaises(ValueError):
            self.lib.update(r.id, title="")

    def test_delete_removes_the_cover(self):
        r = self.lib.add("Blue Train", cover=JPEG)
        path = self.lib.cover_path(r)
        self.lib.delete(r.id)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            self.lib.get(r.id)

    def test_unknown_or_invalid_ids(self):
        for bad in (99, "abc", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.lib.get(bad)

    def test_find_by_source(self):
        r = self.lib.add("Blue Train", source="itunes", source_ref="1234")
        self.assertEqual(self.lib.find("itunes", "1234"), r)
        self.assertIsNone(self.lib.find("musicbrainz", "1234"))
        self.assertIsNone(self.lib.find("", ""))


if __name__ == "__main__":
    unittest.main()
