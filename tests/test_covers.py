"""Cover sources against canned answers (no network): search parsing, cover choice, retries."""

import json
import unittest
import urllib.parse
from typing import Dict, List, Tuple

from wormhole import covers
from wormhole.covers import COVER_BYTES_MAX, CoverError, ITunes, MusicBrainz, image_kind

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 1000
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 1000

MB_SEARCH = {"count": 2, "release-groups": [
    {"id": "11111111-2222-3333-4444-555555555555", "title": "Blue Train",
     "primary-type": "Album", "first-release-date": "1958-01",
     "artist-credit": [{"name": "John Coltrane"}]},
    {"id": "aaaaaaaa-2222-3333-4444-555555555555", "title": "Live Trane",
     "primary-type": "Album", "secondary-types": ["Live", "Compilation"],
     "artist-credit": [{"name": "John Coltrane", "joinphrase": " & "},
                       {"name": "The Quartet"}]},
    {"title": "no id: skipped"},
]}

IT_SEARCH = {"resultCount": 1, "results": [
    {"collectionId": 1234, "collectionName": "Blue Train (Remastered)",
     "artistName": "John Coltrane", "releaseDate": "1957-09-15T07:00:00Z", "trackCount": 7,
     "artworkUrl100": "https://is1.example/image/thumb/a/b.jpg/100x100bb.jpg"}]}


class FakeWeb:
    """fetch() stand-in: answers by URL prefix, records every request."""

    def __init__(self, answers: Dict[str, List[Tuple[int, bytes]]]):
        self.answers = answers
        self.requests: List[Tuple[str, Dict[str, str]]] = []

    def __call__(self, url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, bytes]:
        self.requests.append((url, headers))
        for prefix, replies in self.answers.items():
            if url.startswith(prefix):
                return replies.pop(0) if len(replies) > 1 else replies[0]
        return 404, b"not found"


def as_json(data) -> Tuple[int, bytes]:
    return 200, json.dumps(data).encode()


def query_of(url: str) -> Dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


class MusicBrainzTest(unittest.TestCase):
    def setUp(self):
        MusicBrainz.spacing, MusicBrainz.retry_pause = 0, 0

    def tearDown(self):
        MusicBrainz.spacing, MusicBrainz.retry_pause = 1.1, 1.5

    def source(self, answers, cfg=None):
        self.web = FakeWeb(answers)
        return MusicBrainz(cfg or {}, self.web)

    def test_search_results(self):
        src = self.source({MusicBrainz.api: [as_json(MB_SEARCH)]})
        found = src.search("Blue Train", "Coltrane")
        self.assertEqual(len(found), 2)
        first, second = found
        self.assertEqual((first.title, first.artist, first.year, first.kind),
                         ("Blue Train", "John Coltrane", "1958", "Album"))
        self.assertEqual(first.thumb, "https://coverartarchive.org/release-group/"
                                      "11111111-2222-3333-4444-555555555555/front-250")
        self.assertEqual(second.artist, "John Coltrane & The Quartet")
        self.assertEqual(second.kind, "Album · Live · Compilation")
        self.assertEqual(second.year, "")

    def test_fielded_query_with_escaping(self):
        src = self.source({MusicBrainz.api: [as_json(MB_SEARCH)]})
        src.search('  AC/DC: "Live"  ', "")
        q = query_of(self.web.requests[0][0])
        self.assertEqual(q["query"], r'releasegroup:(AC\/DC\: \"Live\")')
        self.assertEqual(q["fmt"], "json")
        src.search("Blue Train", "John Coltrane")
        self.assertEqual(query_of(self.web.requests[1][0])["query"],
                         "releasegroup:(Blue Train) AND artist:(John Coltrane)")

    def test_empty_search_refused(self):
        with self.assertRaises(ValueError):
            self.source({}).search(" ", "")

    def test_user_agent_contact_only_when_set(self):
        src = self.source({MusicBrainz.api: [as_json(MB_SEARCH)]})
        src.search("x", "")
        self.assertEqual(self.web.requests[0][1]["User-Agent"],
                         f"wormhole/{covers.__version__}")
        src = self.source({MusicBrainz.api: [as_json(MB_SEARCH)]},
                          {"COVER_CONTACT": "https://example.org/wormhole"})
        src.search("x", "")
        self.assertTrue(self.web.requests[0][1]["User-Agent"].endswith(
            "( https://example.org/wormhole )"))

    def test_busy_is_retried(self):
        src = self.source({MusicBrainz.api: [(503, b"busy"), as_json(MB_SEARCH)]})
        self.assertEqual(len(src.search("x", "")), 2)
        self.assertEqual(len(self.web.requests), 2)

    def test_busy_for_good(self):
        src = self.source({MusicBrainz.api: [(503, b"busy")]})
        with self.assertRaisesRegex(CoverError, "busy"):
            src.search("x", "")
        self.assertEqual(len(self.web.requests), 1 + MusicBrainz.retries)

    def test_unreachable(self):
        def down(url, headers, timeout):
            raise OSError("no route to host")
        with self.assertRaisesRegex(CoverError, "not reachable"):
            MusicBrainz({}, down).search("x", "")

    def test_garbage_answer(self):
        with self.assertRaises(CoverError):
            self.source({MusicBrainz.api: [(200, b"<html>")]}).search("x", "")

    def test_cover_biggest_first(self):
        ref = "11111111-2222-3333-4444-555555555555"
        src = self.source({f"{MusicBrainz.covers}{ref}/front-1200": [(200, JPEG)]})
        self.assertEqual(src.cover(ref), JPEG)

    def test_cover_too_big_falls_back_to_smaller(self):
        ref = "11111111-2222-3333-4444-555555555555"
        huge = b"\xff\xd8\xff" + b"x" * COVER_BYTES_MAX
        src = self.source({f"{MusicBrainz.covers}{ref}/front-1200": [(200, huge)],
                           f"{MusicBrainz.covers}{ref}/front-500": [(200, PNG)]})
        self.assertEqual(src.cover(ref), PNG)

    def test_no_cover(self):
        with self.assertRaisesRegex(CoverError, "no cover"):
            self.source({}).cover("11111111-2222-3333-4444-555555555555")

    def test_cover_ref_is_checked(self):
        with self.assertRaises(ValueError):
            self.source({}).cover("../../etc/passwd")


class ITunesTest(unittest.TestCase):
    def source(self, answers, cfg=None):
        self.web = FakeWeb(answers)
        return ITunes(cfg or {}, self.web)

    def test_search_results(self):
        src = self.source({ITunes.api + "search": [as_json(IT_SEARCH)]}, {"ITUNES_COUNTRY": "ch"})
        (a,) = src.search("Blue Train", "John Coltrane")
        self.assertEqual((a.ref, a.title, a.artist, a.year, a.kind),
                         ("1234", "Blue Train (Remastered)", "John Coltrane", "1957", "7 tracks"))
        self.assertEqual(a.thumb, "https://is1.example/image/thumb/a/b.jpg/250x250bb.jpg")
        q = query_of(self.web.requests[0][0])
        self.assertEqual((q["term"], q["entity"], q["country"]),
                         ("John Coltrane Blue Train", "album", "CH"))

    def test_cover_from_lookup(self):
        src = self.source({ITunes.api + "lookup": [as_json(IT_SEARCH)],
                           "https://is1.example/image/thumb/a/b.jpg/1200x1200bb.jpg":
                               [(200, JPEG)]})
        self.assertEqual(src.cover("1234"), JPEG)
        self.assertEqual(query_of(self.web.requests[0][0])["id"], "1234")

    def test_lookup_without_artwork(self):
        with self.assertRaisesRegex(CoverError, "no cover"):
            self.source({ITunes.api + "lookup": [as_json({"results": []})]}).cover("1234")

    def test_cover_ref_is_checked(self):
        with self.assertRaises(ValueError):
            self.source({}).cover("12; rm")


class HelpersTest(unittest.TestCase):
    def test_image_kind(self):
        self.assertEqual(image_kind(JPEG), "jpg")
        self.assertEqual(image_kind(PNG), "png")
        self.assertIsNone(image_kind(b"GIF89a"))
        self.assertIsNone(image_kind(b""))

    def test_configured_order_and_unknown_names(self):
        with self.assertLogs("wormhole.covers", "WARNING"):
            found = covers.configured({"COVER_SOURCES": "itunes, nope ,musicbrainz"})
        self.assertEqual(list(found), ["itunes", "musicbrainz"])
        self.assertEqual(list(covers.configured({})), list(covers.SOURCES))


if __name__ == "__main__":
    unittest.main()
