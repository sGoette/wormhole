# OwnTone (Phases 3 and 5)

`owntone.conf` (or the relevant excerpt) for the NAS goes here once the jail exists.
The Mac template [tools/owntone-mac/owntone.conf.in](../../tools/owntone-mac/owntone.conf.in)
is the starting point.

## Verified (OwnTone 29.3 source, 2026-11-13)

- Pipe settings live in the `library` section: `pipe_autostart` (default `true`),
  `pipe_sample_rate` (default 44100; accepted 44100, 48000, 88200, 96000),
  `pipe_bits_per_sample` (default 16; accepted 16, 32). Always 2 channels, host byte order.
- The pipe must be a named pipe (FIFO) inside one of the library `directories`.
- JSON API used by `src/wormhole/owntone.py`: `GET /api/outputs`, `PUT /api/outputs/set`,
  `PUT /api/outputs/{id}` (`selected`, `volume`, `pin`), `GET /api/player`,
  `PUT /api/player/{play,pause,stop}`.
- FreeBSD: `pkg install owntone` (ports `audio/owntone`), per the OwnTone install docs.

## Still open (STRATEGY.md §5.3)

Whether the ports version handles AirPlay 2 HomePods cleanly, given the 2024 crash report.
Check `pkg info owntone` version against 29.3 before relying on it.
