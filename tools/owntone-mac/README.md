# OwnTone on the Mac (Phase 3)

There is no Homebrew formula for OwnTone, so `build.sh` compiles **OwnTone 29.4** from the
release tarball into `.local/owntone/` (git-ignored). Only the Homebrew dependencies are
installed system-wide. The steps follow OwnTone's own macOS CI workflow.

```bash
make owntone-build          # once (~2 min); safe to re-run
make owntone-run            # terminal 1: OwnTone in the foreground, web UI http://localhost:3689
```

| File | Purpose |
|---|---|
| `build.sh` | Homebrew deps, libinotify-kqueue 20240724, OwnTone 29.4 (checksums pinned) |
| `run.sh` | Renders `config/owntone.conf.in` (shared with the server) from `config/local.env`, creates the pipe, starts OwnTone |

Library = `.local/owntone/library/` (holds the `turntable` named pipe) + `tools/media/`.

## Phase 3 test steps

Easiest: two terminals, then everything in the browser (also works from the iPhone):

```bash
make owntone-run            # terminal 1
make web                    # terminal 2 → http://localhost:8766
```

The page lists every speaker with an on/off switch and its own volume slider, and starts or
stops the feed into the pipe (tone, a file from `tools/media/`, or the fake turntable URL).

`make web` also starts the UPnP renderer **"Wormhole (Mac)"** (`RENDERER_NAME` in
`config/local.env`). It shows up as a speaker in the fake turntable (`make turntable`),
Bubble UPnP and, presumably, the Pro-Ject Control app. Play there → the stream goes through
the feed into OwnTone → selected HomePods. The web UI shows the renderer's state.

Same steps on the command line:

```bash
bin/wormhole owntone outputs                         # HomePods should be listed
bin/wormhole owntone select Kitchen --volume 25      # one HomePod
make feed                                            # terminal 2: 440 Hz tone into the pipe
bin/wormhole owntone status
bin/wormhole owntone select Kitchen Bedroom          # two HomePods as a group
make feed INPUT=http://localhost:8765/live/tone/stream.mp3   # fake turntable -> pipe
bin/wormhole owntone stop
```

- `pipe_autostart` is on: OwnTone starts playing as soon as `feed` writes into the pipe.
- A local file test (STRATEGY.md Phase 3, first bullet): use the OwnTone web UI and play a
  file from `tools/media/`.
- If a speaker shows "needs PIN/password", it displays a PIN on selection:
  `bin/wormhole owntone pin <name> <pin>`.
- Log: `.local/owntone/var/owntone.log`. Copy exact errors into `RESULTS.md`.
- macOS may ask to allow incoming connections for `owntone`: allow it (AirPlay 2 speakers
  connect back to it).
