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

## Required for HomePods on software 27+

`user_agent = "AirPlay/999.0.0"` in the `general` section. Without it, HomePods answer
`403 Forbidden` to `GET /info` and refuse to activate. Verified on a HomePod on 2026-11-13;
see OwnTone issue #2042. OwnTone versions after 29.3 send this by default. The FreeBSD port
is likely 29.3 or older, so set it explicitly there.

## Pipe autostart is not reliable after a stop

After `PUT /api/player/stop`, the pipe stays the player's current item, and OwnTone's
autostart (`pipe_read_cb` in `src/inputs/pipe.c`) treats new data as "already playing". Even
with the queue cleared, the re-armed pipe watch didn't fire again in our tests on macOS
(2026-11-13), though timing changed the outcome. So `src/wormhole/owntone.py`:
- `stop()` = `PUT /api/player/stop` + `PUT /api/queue/clear`
- `play_pipe()` = `POST /api/queue/items/add?uris=<pipe>&clear=true&playback=start`, sent
  by the feeder when the **first audio data** arrives (`feed.Feeder(on_first_data=…)`).
  Sending it earlier (before data) works for the fifo output but left the HomePods silent with
  FLAC, which takes ~3 s to start, longer than OwnTone's `start_buffer_ms` (2250 ms).
Recheck on FreeBSD; autostart may behave differently with a different event backend.

## Still open (STRATEGY.md §5.3)

Whether the ports version handles AirPlay 2 HomePods cleanly, given the 2024 crash report.
Check `pkg info owntone` version against 29.3 before relying on it.
