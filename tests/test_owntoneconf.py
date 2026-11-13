import tempfile
import unittest
from pathlib import Path

from wormhole import owntoneconf

CFG = {"OWNTONE_URL": "http://localhost:3689", "PCM_PIPE": "/var/lib/wormhole/library/turntable",
       "PIPE_SAMPLE_RATE": "48000", "PIPE_BITS": "16", "LOG_LEVEL": "DEBUG"}


class RenderTest(unittest.TestCase):
    def render(self, **kw):
        args = dict(user="owntone", data_dir=Path("/var/cache/owntone"),
                    logfile=Path("/var/log/owntone.log"), name="wormhole on %h")
        args.update(kw)
        return owntoneconf.render(CFG, **args)

    def test_server_values(self):
        text = self.render()
        for line in ('uid = "owntone"', 'db_path = "/var/cache/owntone/songs3.db"',
                     'logfile = "/var/log/owntone.log"', 'name = "wormhole on %h"',
                     "port = 3689", 'directories = { "/var/lib/wormhole/library" }',
                     "pipe_sample_rate = 48000", "pipe_bits_per_sample = 16",
                     "loglevel = info", 'user_agent = "AirPlay/999.0.0"'):
            self.assertIn(line, text)
        self.assertNotIn("@", text.replace("%h", ""))

    def test_extra_library_dirs(self):
        text = self.render(extra_dirs=[Path("/m/media")])
        self.assertIn('directories = { "/var/lib/wormhole/library", "/m/media" }', text)

    def test_quotes_rejected(self):
        with self.assertRaises(ValueError):
            self.render(name='bad "name"')

    def test_unfilled_placeholder_detected(self):
        with tempfile.TemporaryDirectory() as d:
            tpl = Path(d) / "t.in"
            tpl.write_text("general { uid = \"@USER@\"\n x = @NEW_KEY@ }\n")
            with self.assertRaisesRegex(ValueError, "@NEW_KEY@"):
                self.render(template=tpl)


if __name__ == "__main__":
    unittest.main()
