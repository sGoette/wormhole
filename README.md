# wormhole

Turntable → UPnP renderer → PCM pipe → OwnTone → HomePods, controlled from a web UI.
The plan lives in [.claude/STRATEGY.md](.claude/STRATEGY.md); test results go in `RESULTS.md`
(local only, git-ignored: it names devices and addresses from the home network).

## Layout

| Path | Runs on | Purpose |
|---|---|---|
| `src/wormhole/` | Mac **and** server | Python package (stdlib only): renderer, feed, OwnTone client, web API, updates |
| `src/wormhole/frontend/` | browser | Web UI: HTML, CSS, JavaScript modules, icons (no build step) |
| `bin/wormhole` | Mac **and** server | Launcher: sets `PYTHONPATH`, so no install step is needed |
| `config/` | both | `local.env` = Mac settings; `owntone.conf.in` = OwnTone config template |
| `server/` | server | Debian VM: `install.sh`, systemd unit, settings example, setup guide |
| `tools/` | Mac only | Test stand-ins: `fake_turntable/`, OwnTone build for the Mac, icon generator |
| `tests/` | both | `unittest` tests (stdlib, no pytest needed) |
| `BACKLOG.md` | — | Features decided for later |
| `results/` | Mac | Raw logs/captures referenced from `RESULTS.md` (git-ignored) |
| `.claude/` | — | `CLAUDE.md` (structure + code style, loaded by Claude Code) and `STRATEGY.md` |

Rule of thumb: anything that runs on the server goes in `src/wormhole/` and reads its settings
from an env file (`config/local.env` on the Mac, `/etc/wormhole/wormhole.env` on the server):
never hard-code hosts, ports or paths. The same code is tested on the Mac first.

## On the Mac

```bash
make test             # unit tests
make turntable        # fake turntable (stands in for the Pro-Ject T2 W)
make owntone-build    # once: OwnTone into .local/owntone
make owntone-run      # OwnTone in the foreground
make web              # web UI + UPnP renderer "Wormhole (Mac)": http://localhost:8766
```

## On the server

A headless Debian 13 VM on TrueNAS CORE, installed from GitHub and updated from the web UI
(Software panel). Step-by-step: [server/README.md](server/README.md).
