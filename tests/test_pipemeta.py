"""Metadata pipe: the Shairport Sync item format OwnTone parses, and writing to a real FIFO."""

import base64
import os
import re
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from wormhole.pipemeta import PICTURE_MAX, MetadataPipe, item, items

ITEM = re.compile(r"<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code>"
                  r"<length>(\d+)</length>\n<data encoding=\"base64\">\n([A-Za-z0-9+/=]*)"
                  r"</data></item>\n")
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100_000  # bigger than a pipe buffer


def parse(data: bytes):
    """(type, code, payload) per item, as OwnTone's parse_item_xml reads them."""
    out = []
    for kind, code, length, payload in ITEM.findall(data.decode()):
        raw = base64.b64decode(payload)
        assert len(raw) == int(length)
        out.append((bytes.fromhex(kind).decode(), bytes.fromhex(code).decode(), raw))
    return out


class ItemTest(unittest.TestCase):
    def test_item_format(self):
        self.assertEqual(parse(item("minm", "Abbey Road".encode())),
                         [("core", "minm", b"Abbey Road")])

    def test_update_items(self):
        got = parse(items("Blue Train", "John Coltrane", JPEG))
        self.assertEqual([(k, c) for k, c, _ in got],
                         [("core", "minm"), ("core", "asar"), ("ssnc", "PICT")])
        self.assertEqual(got[1][2], "John Coltrane".encode())
        self.assertEqual(got[2][2], JPEG)

    def test_empty_values_are_left_out(self):
        # OwnTone ignores items without data anyway (pipe.c, "Missing ... payload").
        self.assertEqual([c for _, c, _ in parse(items("Turntable", ""))], ["minm"])
        self.assertEqual(items("", ""), b"")

    def test_non_ascii_text(self):
        self.assertEqual(parse(items("Für Elise", ""))[0][2].decode(), "Für Elise")

    def test_picture_too_big_for_owntone(self):
        with self.assertRaises(ValueError):
            items("x", "y", b"\xff\xd8" + b"x" * PICTURE_MAX)


class PipeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pcm = Path(tmp.name) / "library" / "turntable"
        self.pipe = MetadataPipe.for_pcm_pipe(self.pcm)

    def test_named_after_the_pcm_pipe(self):
        self.assertEqual(self.pipe.path.name, "turntable.metadata")

    def test_ensure_creates_a_readable_fifo(self):
        self.pipe.ensure()
        mode = self.pipe.path.stat().st_mode
        self.assertTrue(stat.S_ISFIFO(mode))
        self.assertEqual(stat.S_IMODE(mode), 0o644)
        self.pipe.ensure()  # again: fine

    def test_ensure_refuses_a_regular_file(self):
        self.pcm.parent.mkdir(parents=True)
        self.pipe.path.write_text("not a pipe")
        with self.assertRaises(OSError):
            self.pipe.ensure()

    def test_no_reader_is_not_an_error(self):
        self.pipe.ensure()
        self.assertFalse(self.pipe.send("Abbey Road", "The Beatles"))

    def test_missing_fifo_is_not_an_error(self):
        self.assertFalse(self.pipe.send("Abbey Road", "The Beatles"))

    def test_send_to_a_reader(self):
        self.pipe.ensure()
        received = bytearray()
        opened = threading.Event()

        def reader():
            # Like OwnTone: non-blocking open for reading, then read until the writer closes.
            fd = os.open(self.pipe.path, os.O_RDONLY | os.O_NONBLOCK)
            opened.set()
            os.set_blocking(fd, True)
            try:
                while True:
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk and received:
                        break
                    received.extend(chunk)
            finally:
                os.close(fd)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        opened.wait(2)
        self.assertTrue(self.pipe.send("Blue Train", "John Coltrane", JPEG))
        t.join(5)
        self.assertEqual([c for _, c, _ in parse(bytes(received))], ["minm", "asar", "PICT"])
        self.assertEqual(parse(bytes(received))[2][2], JPEG)


if __name__ == "__main__":
    unittest.main()
