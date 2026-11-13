# wormhole — project guide for Claude

Turntable → UPnP renderer (FreeBSD NAS jail) → PCM pipe → OwnTone → HomePods.
The plan and phase order are in [STRATEGY.md](STRATEGY.md); read the relevant phase before
working on it. Test outcomes go in [../RESULTS.md](../RESULTS.md).

## Working rules

- Verify before asserting: FreeBSD package names, OwnTone config keys and API endpoints come
  from the docs or the tools, never from memory.
- Test on the Mac first; the NAS is for what can only be tested there.
- Ask before any step that changes NAS config or installs system packages on it. Deploying the
  repo to the NAS (`make deploy`) is also an outward action: confirm unless told to proceed.
- Log every test in `RESULTS.md` (template at the top): date, device, versions, command,
  observed result. On failure, record the exact error before changing anything; change one
  thing at a time.
- No pip dependencies without the user's agreement.
- Commit only when asked. The user writes in English and is in Europe/Zurich.

## File structure

| Path | Runs on | Put here |
|---|---|---|
| `src/wormhole/` | Mac + NAS | All code that may run on the NAS (bridge, controller). Shipped by deploy. |
| `src/wormhole/__main__.py` | Mac + NAS | CLI: one subcommand per feature (`info`, later `bridge`, `controller`, …). |
| `src/wormhole/config.py` | Mac + NAS | `KEY=VALUE` env loader; env vars override; relative paths → repo root. |
| `src/wormhole/feed.py` | Mac + NAS | `wormhole feed`: input → ffmpeg → raw PCM → OwnTone named pipe, auto-restart. |
| `src/wormhole/owntone.py` | Mac + NAS | OwnTone JSON API client; `wormhole owntone outputs/select/status/…`. |
| `bin/wormhole` | Mac + NAS | POSIX sh launcher; sets `PYTHONPATH`, picks `config/active.env` → `config/local.env`. |
| `config/local.env` | Mac | Mac runtime settings. |
| `config/nas.env.example` | — | Template for `config/nas.env` (git-ignored): deploy target + NAS runtime settings. |
| `nas/` | NAS | Jail notes, renderer + OwnTone config, `rc.d` scripts. Shipped by deploy. |
| `tools/` | Mac | Test stand-ins. Never deployed. Usage in `tools/README.md`. |
| `tools/fake_turntable.py` | Mac | T2 W stand-in: live MP3/FLAC stream + UPnP control + web UI. |
| `tools/media/` | Mac | Test audio for the fake turntable (audio files git-ignored). |
| `tools/owntone-mac/` | Mac | Build (`build.sh`), config template, `run.sh` for OwnTone 29.3 on the Mac. |
| `.local/` | Mac | Git-ignored build output + runtime data (`.local/owntone/`: binary, db, log, pipe). |
| `deploy/deploy.sh` | Mac | deploy / dry-run / rollback / status / logs / ssh. Ships `bin src nas` + config. |
| `scripts/` | Mac | Local helpers (`check-prereqs.sh` = Phase 0). |
| `tests/` | Mac + NAS | `unittest` tests, `test_*.py`. |
| `results/logs/` | Mac | Raw logs referenced from `RESULTS.md` (contents git-ignored). |
| `.claude/` | — | `CLAUDE.md` (this file, auto-loaded), `STRATEGY.md`; `settings.local.json` git-ignored. |

On the NAS: `$NAS_PATH/releases/<timestamp>-<sha>/`, `$NAS_PATH/current` → live release,
`current/config/active.env` = copy of `config/nas.env`, `current/VERSION` = release name.

When adding a directory or a new kind of file, update this table and the README layout table.

## Code style

### Python (`src/`, `tools/`, `tests/`)
- Python **3.9+**, **standard library only**. No `match`, no `X | Y` type unions at runtime,
  no 3.10+ stdlib APIs — the NAS Python version is not confirmed yet.
- Type hints on function signatures, using `typing` (`Dict`, `Optional`, `List`) for 3.9.
- No hard-coded hosts, ports, paths or device names: read them from `config.load()`; add new
  keys to **both** `config/local.env` and `config/nas.env.example`. Settings are `UPPER_SNAKE`.
- Small modules with one job; a module per feature under `src/wormhole/`, wired up as a CLI
  subcommand in `__main__.py`. Commands return an exit code (`int`).
- Logging via `logging`, level from `LOG_LEVEL`; `print` only for CLI output meant for the user.
- Long-running services must handle SIGTERM/Ctrl+C cleanly (stop playback, close pipes).
- Docstrings: a one-line module docstring stating purpose; comments explain *why*, not *what*.
- 4-space indent, double quotes, lines ≤ 100 chars, f-strings.
- `tools/fake_turntable.py` stays a single standalone file (no import from `src/`).

### Shell (`bin/`, `deploy/`, `scripts/`, `nas/rc.d/`)
- POSIX `#!/bin/sh` — must run on FreeBSD `/bin/sh` and macOS. No bashisms (`[[ ]]`, arrays,
  `local`-dependent logic, `echo -e`).
- `set -eu` in scripts that change things; quote all variables.
- Commands that run remotely must work with FreeBSD userland (BSD `ln`, `sed`, `readlink`).
- Mac rsync is openrsync: stick to common flags (`-rlptz`, `--delete`, `-n`, `-v`).

### Tests
- `unittest`, run with `make test`. Every new module in `src/wormhole/` gets a test file.
- Tests must not need the network, ffmpeg or real devices; hardware checks are manual and
  go in `RESULTS.md`.

### Git
- Branch `main`. Short imperative subject line, body explains why.
- Never commit `config/nas.env`, logs or captures.

## Commands

```bash
make test | make check | make run ARGS="info"
make turntable | make owntone-build | make owntone-run | make feed INPUT=tone|file|URL
bin/wormhole owntone outputs | select NAME... [--volume N] | status | stop | pin NAME PIN
make deploy | make deploy-dry | make rollback | make nas-status | make nas-logs | make nas-ssh
DEPLOY_ENV=path/to/other.env make deploy   # different target
```
