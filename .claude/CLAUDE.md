# wormhole — project guide for Claude

Turntable → UPnP renderer → PCM pipe → OwnTone → HomePods, controlled from a web UI.
Runs on the Mac for development and on a headless Debian 13 VM (TrueNAS CORE) as the server.
The plan and phase order are in [STRATEGY.md](STRATEGY.md); read the relevant phase before
working on it. Test outcomes go in [../RESULTS.md](../RESULTS.md).

## Working rules

- Verify before asserting: Debian package names, OwnTone config keys and API endpoints come
  from the docs, the packages or the tools, never from memory.
- Test on the Mac first; the server is for what can only be tested there.
- There is **no SSH access** to the server. The user installs from GitHub (`server/README.md`)
  and updates from the web UI. Anything that must run on the server goes into
  `server/install.sh` (root, re-runnable) or the app itself, with exact console steps for the
  user.
- Log every test in `RESULTS.md` (template at the top): date, device, versions, command,
  observed result. On failure, record the exact error before changing anything; change one
  thing at a time.
- No pip dependencies without the user's agreement.
- Commit only when asked. Pushing to GitHub is what the server updates from: only when asked.
- The user writes in English and is in Europe/Zurich.

## File structure

| Path | Runs on | Put here |
|---|---|---|
| `src/wormhole/` | Mac + server | All code that runs on the server. |
| `src/wormhole/__main__.py` | Mac + server | CLI: one subcommand per feature (`web`, `feed`, `owntone`, `update`, …). |
| `src/wormhole/config.py` | Mac + server | `KEY=VALUE` env loader; env vars override; relative paths → repo root. |
| `src/wormhole/feed.py` | Mac + server | Input → ffmpeg → relay into OwnTone's pipe; starts OwnTone on first audio. |
| `src/wormhole/owntone.py` | Mac + server | OwnTone JSON API client; `wormhole owntone outputs/select/status/…`. |
| `src/wormhole/owntoneconf.py` | Mac + server | Renders `config/owntone.conf.in` (`wormhole owntone-config`). |
| `src/wormhole/renderer.py` | Mac + server | Own UPnP MediaRenderer (SSDP, AVTransport, RC, CM, GENA); Play → shared feed. |
| `src/wormhole/update.py` | Mac + server | git-based updates: check / fast-forward / rollback (`wormhole update`, web UI). |
| `src/wormhole/web.py` | Mac + server | `wormhole web`: speakers, volumes, PIN pairing, feed, Software panel; starts the renderer; re-execs itself after updates. |
| `bin/wormhole` | Mac + server | POSIX sh launcher; sets `PYTHONPATH`; config `$WORMHOLE_ENV` → `/etc/wormhole/wormhole.env` → `config/local.env`. |
| `config/local.env` | Mac | Mac settings. |
| `config/owntone.conf.in` | both | OwnTone config template (HomePod User-Agent fix, pipe settings). |
| `server/install.sh` | server | Root, re-runnable: apt + OwnTone .deb (pinned), users, pipe, configs, systemd. |
| `server/wormhole.service` | server | systemd unit (`@REPO@` filled in by install.sh). |
| `server/wormhole.env.example` | server | Settings; copied once to `/etc/wormhole/wormhole.env`. Same keys as `config/local.env`. |
| `server/README.md` | — | VM setup guide, update steps, OwnTone notes. |
| `tools/` | Mac | Test stand-ins. Never on the server. Usage in `tools/README.md`. |
| `tools/fake_turntable.py` | Mac | T2 W stand-in: live MP3/FLAC stream + UPnP control + web UI. |
| `tools/media/` | Mac | Test audio for the fake turntable (audio files git-ignored). |
| `tools/owntone-mac/` | Mac | Build (`build.sh`) and `run.sh` for OwnTone 29.3 on the Mac. |
| `.local/` | Mac | Git-ignored build output + runtime data (`.local/owntone/`: binary, db, log, pipe). |
| `scripts/` | Mac | Local helpers (`check-prereqs.sh` = Phase 0). |
| `tests/` | Mac | `unittest` tests, `test_*.py`. |
| `RESULTS.md` | Mac | Test log. Git-ignored: it holds device names, IDs and LAN addresses. |
| `results/logs/` | Mac | Raw logs referenced from `RESULTS.md` (contents git-ignored). |
| `.claude/` | — | `CLAUDE.md` (this file, auto-loaded), `STRATEGY.md`; `settings.local.json` git-ignored. |

On the server: code in `/opt/wormhole` (git clone owned by user `wormhole`), settings in
`/etc/wormhole/wormhole.env`, OwnTone config `/etc/owntone.conf` (rendered), pipe
`/var/lib/wormhole/library/turntable`, services `owntone` + `wormhole` (systemd).
Changes under `server/` or to `config/owntone.conf.in` need `install.sh` re-run by the user;
the update panel detects and says so.

When adding a directory or a new kind of file, update this table and the README layout table.

## Code style

### Python (`src/`, `tools/`, `tests/`)
- Python **3.9+**, **standard library only** (server: Debian 13 has 3.13; Mac 3.14). No
  `match`, no `X | Y` type unions at runtime.
- Type hints on function signatures, using `typing` (`Dict`, `Optional`, `List`) for 3.9.
- No hard-coded hosts, ports, paths or device names: read them from `config.load()`; add new
  keys to **both** `config/local.env` and `server/wormhole.env.example` (a test checks this).
  Settings are `UPPER_SNAKE`.
- Small modules with one job; a module per feature under `src/wormhole/`, wired up as a CLI
  subcommand in `__main__.py`. Commands return an exit code (`int`).
- Logging via `logging`, level from `LOG_LEVEL`; `print` only for CLI output meant for the user.
- Long-running services must handle SIGTERM/Ctrl+C cleanly (stop playback, close pipes).
- Docstrings: a one-line module docstring stating purpose; comments explain *why*, not *what*.
- 4-space indent, double quotes, lines ≤ 100 chars, f-strings.
- `tools/fake_turntable.py` stays a single standalone file (no import from `src/`).
- The web page lives in `web.PAGE` (a normal Python string): write `\\n` for a JS newline.

### Shell (`bin/`, `server/`, `scripts/`, `tools/owntone-mac/`)
- POSIX `#!/bin/sh`: must run with Debian's `dash` and macOS `sh`. No bashisms (`[[ ]]`,
  arrays, `echo -e`).
- `set -eu` in scripts that change things; quote all variables.
- `server/install.sh` targets Debian 13 (GNU userland) and must stay re-runnable.

### Tests
- `unittest`, run with `make test`. Every new module in `src/wormhole/` gets a test file.
- Tests must not need the network, ffmpeg or real devices (local git repos are fine);
  hardware checks are manual and go in `RESULTS.md`.

### Git
- Branch `main`. Short imperative subject line, body explains why.
- Never commit logs, captures, audio files, `.local/` or `RESULTS.md`. Committed files use
  neutral examples ("Kitchen HomePod", `192.0.2.x`), never real device names, IDs or LAN
  addresses from the user's network.

## Commands

```bash
make test | make check | make run ARGS="info"
make turntable | make owntone-build | make owntone-run | make web | make feed INPUT=…
bin/wormhole owntone outputs | select NAME... [--volume N] | status | stop | pin NAME PIN
bin/wormhole update check | apply | rollback
bin/wormhole owntone-config --user U --data-dir D --logfile L [--dir EXTRA] [-o FILE]
```
