# wormhole

Turntable → UPnP renderer (NAS jail) → PCM pipe → OwnTone → HomePods.
The plan lives in [.claude/STRATEGY.md](.claude/STRATEGY.md); test results go in [RESULTS.md](RESULTS.md).

## Layout

| Path | Runs on | Purpose |
|---|---|---|
| `tools/` | Mac only | Test tools that stand in for real hardware (`fake_turntable.py`) |
| `src/wormhole/` | Mac **and** NAS | Python package (stdlib only): config, pipeline bridge (Phase 5), controller (Phase 6) |
| `bin/wormhole` | Mac **and** NAS | Launcher — sets `PYTHONPATH`, so no install step is needed anywhere |
| `config/` | both | `local.env` = Mac settings, `nas.env` = NAS settings (copied from `nas.env.example`, not committed) |
| `nas/` | NAS only | Jail notes, renderer + OwnTone config, `rc.d` service scripts |
| `deploy/` | Mac | `deploy.sh`: push a new release to the NAS, flip `current`, restart, smoke-test; rollback |
| `scripts/` | Mac | Helpers, e.g. Phase 0 prerequisite check |
| `tests/` | both | `unittest` tests (stdlib, no pytest needed) |
| `results/logs/` | Mac | Raw logs/captures referenced from `RESULTS.md` |
| `.claude/` | — | `CLAUDE.md` (structure + code style, loaded automatically by Claude Code) and `STRATEGY.md` |

Rule of thumb: anything that might run on the NAS goes in `src/wormhole/` and reads its
settings from `config/*.env` — never hard-code hosts, ports or paths. That way the same code
is tested on the Mac first and deployed unchanged.

## Everyday commands

```bash
make check            # Phase 0: python, ffmpeg, local network sanity
make test             # unit tests on the Mac
make run ARGS="info"  # run the package locally with config/local.env

# NAS (needs config/nas.env)
make deploy           # rsync new release → NAS, switch 'current', restart, smoke-test
make deploy-dry       # show what would be copied
make rollback         # point 'current' back to the previous release
make nas-status       # which release is live + 'wormhole info' on the NAS
make nas-logs         # tail the service log
make nas-ssh          # shell on the NAS
```

## How deploys work

Each `make deploy` creates `$NAS_PATH/releases/<timestamp>-<git sha>/` and atomically
re-points `$NAS_PATH/current` at it. The last `KEEP_RELEASES` releases stay on disk, so a bad
try-and-error change is one `make rollback` away. `config/nas.env` is shipped as
`current/config/active.env`, so the code on the NAS reads the NAS settings automatically.
See [nas/jails/README.md](nas/jails/README.md) for how jails see the deployed code.
