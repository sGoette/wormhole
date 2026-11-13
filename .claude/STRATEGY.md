# Vinyl → HomePods via UPnP + FreeBSD NAS: Test Strategy

Working document for the VS Code session. Read this first, then work through the phases in order.

## 1. Goal

Play a turntable through several HomePods in different rooms, controlled from an iPhone, without the iPhone being in the audio path.

Before buying a Pro-Ject T2 W (the intended turntable), prove every link in the chain with test tools on the Mac and, later, on the NAS.

## 2. Target architecture

```
Pro-Ject T2 W ──UPnP push, live MP3 (or FLAC)──▶ UPnP renderer on NAS (FreeBSD jail)
                                                    │  decode to raw PCM
                                                    ▼
                                          named pipe ──▶ OwnTone (FreeBSD jail)
                                                    │  AirPlay 2
                                                    ▼
                                          HomePod(s) in different rooms

Control:
  - Pro-Ject Control app: choose the NAS as receiver (set-up step, not in the audio path)
  - OwnTone web UI or a small Python controller: choose HomePods / rooms
```

**Pre-purchase stand-in:** `fake_turntable.py` (in this folder) replaces the T2 W. It runs on the Mac, serves a live MP3 stream from ffmpeg, and tells a UPnP renderer to play it using standard AVTransport calls (SetAVTransportURI + Play). Once the NAS renderer and OwnTone pass, the T2 W should only change the source; the rest stays the same.

## 3. Facts to keep in mind

- The T2 W is a **UPnP source**. It creates an MP3 or FLAC livestream and hands it to a receiver you select. It is not an AirPlay sender and not a UPnP media server.
- The receiver must be able to play **MP3 live streams**. Verify this on the NAS renderer early; it is the most likely blocker.
- Receivers buffer UPnP livestreams differently, so latency from needle drop to sound will vary. Measure it.
- The T2 W's analog RCA output is separate from the network section. Keep that in mind for a fallback.
- The Pro-Ject Control app discovers receivers over UPnP. Apps such as **Bubble UPnP** or **mConnect Lite** list the same receivers. If they see the NAS renderer, the T2 W should too. Use this as a cheap pre-purchase check.
- **OwnTone** is in the FreeBSD ports tree (`audio/owntone`). It supports AirPlay 1 and 2 (multi-room) and takes pipe input.
- **Discovery is multicast** (SSDP for UPnP, mDNS for AirPlay). Containers or jails on a NAT bridge will not be discovered. Use a VNET jail with its own LAN address, or host networking.
- A FreeBSD bug report from 2024 described OwnTone crashing when an AirPlay target was selected. Check the installed version before relying on it.
- Some HomePod generations and OS versions have had AirPlay 2 handshake issues with third-party senders. Test with the real HomePods.

## 4. Phases

Work through these in order. Record each result in `RESULTS.md` with date, versions, and what was observed. Do not move on until the acceptance criteria are met.

### Phase 0: Mac prerequisites

- Python 3.9+ on the Mac (`python3 --version`)
- ffmpeg: `brew install ffmpeg`
- Terminal (or VS Code) must be allowed under **System Settings → Privacy & Security → Local Network**. Without this, SSDP discovery fails silently.
- If the macOS firewall prompts for Python incoming connections, allow it. The stream server listens on port 8765 by default.

**Acceptance:** `python3 fake_turntable.py --help` runs, and `ffmpeg -version` works.

### Phase 1: Test the stand-in against any existing UPnP renderer

No NAS needed. If you own any UPnP/DLNA renderer (WiiM, Sonos, Denon HEOS, many AV receivers, a smart TV), use it.

```bash
python3 fake_turntable.py --list
python3 fake_turntable.py --renderer "<name or IP>"
python3 fake_turntable.py --renderer "<name or IP>" --stop
```

Also install **Bubble UPnP** on the iPhone and check whether it finds the same renderer. It can also push a stream manually, which gives you a second, independent test tool.

**Acceptance:**
- `--list` shows the renderer with `AVTransport: yes`.
- A test tone plays on the renderer after `Play`.
- `--stop` silences it.
- Ctrl+C during playback also stops it.
- If the renderer rejects the stream, note the exact error and the renderer model, and check whether it needs a `DIDL-Lite` tweak or a different `protocolInfo`.

### Phase 2: Confirm the renderer accepts an MP3 live stream

This is the same test as Phase 1, but it also answers the key question for the NAS renderer: does it play a non-seekable MP3 livestream without a Content-Length?

**Acceptance:** playback runs for at least 15 minutes with no dropouts, and it recovers after the renderer or the Mac's stream server is restarted.

### Phase 3: Mac-side OwnTone and HomePod check (optional, can run in parallel)

Check whether OwnTone can drive your HomePods from a Mac before building anything on FreeBSD. Homebrew may have an `owntone` formula; check with `brew search owntone` and verify it. Then:

- Play a local file through OwnTone to one HomePod, then to two HomePods as a group.
- Feed a test PCM stream into OwnTone's pipe input with ffmpeg (for example, a tone as raw `s16le`, 44.1 kHz, stereo). **Check the OwnTone docs for the pipe's expected format and config key before running this.** I haven't verified the exact defaults.

**Acceptance:** a HomePod plays the pipe input, and the HomePod group stays in sync.

### Phase 4: NAS renderer (FreeBSD jail)

- Create a VNET jail with its own LAN address (iocage or bastille).
- Install a UPnP MediaRenderer that can play an HTTP stream and decode MP3 (a GStreamer-based renderer is typical). **Check the FreeBSD ports tree for an available package before assuming one exists**; I have not confirmed which renderer packages are present.
- Point `fake_turntable.py` at the jail's renderer:

  ```bash
  python3 fake_turntable.py --list
  python3 fake_turntable.py --renderer "<jail renderer name>"
  ```

- Confirm the jail's renderer shows up in discovery and accepts SetAVTransportURI and Play.

**Acceptance:** the renderer plays the stream from the Mac, and it does so after a jail restart.

### Phase 5: Renderer → PCM → OwnTone pipe (in the NAS)

- Decode the renderer's output to raw PCM and write it to the pipe that OwnTone reads. Use ffmpeg or GStreamer. The exact command depends on the renderer and OwnTone's pipe config, so verify both.
- If the renderer exposes a hook for its decoded output, use it. Otherwise, a small Python wrapper can manage the pipeline and restart it on failure.

**Acceptance:** with the T2 W-equivalent stream running, audio reaches a HomePod through OwnTone, and it survives a 1-hour soak.

### Phase 6: Python controller

Build a small service that:

- Starts and stops the pipeline
- Lists and selects HomePods through OwnTone's HTTP API (verify endpoints in the OwnTone docs; don't guess)
- Shows state (renderer connected? stream present? which rooms active?)
- Optionally exposes a simple web UI for the iPhone (Safari bookmark is enough)

**Acceptance:** from the iPhone, a user can pick rooms and see whether audio is flowing, with no AirPlay from the phone.

### Phase 7: Reliability and latency

- Measure latency from record start to sound on each HomePod.
- Soak test for at least 2 hours with the Mac's Wi-Fi dropping for 30 seconds mid-session.
- Test recovery after each component restart: renderer, OwnTone, the Python controller, the NAS.

**Acceptance:** recovery is automatic or takes one tap, and the HomePods stay in sync.

### Phase 8: Buy decision

If Phases 1 to 5 pass with the test tools, buy the T2 W and repeat Phase 2 with the real device. Then go to Phase 6.

## 5. Open questions to verify

1. Which UPnP renderer packages are available in the FreeBSD ports tree, and do they play MP3 live streams?
2. OwnTone's pipe input: expected format, config key, and whether it takes a named pipe or stdin.
3. Does the OwnTone version in the ports tree handle AirPlay 2 HomePods cleanly, given the 2024 crash report?
4. The T2 W's exact MP3 or FLAC livestream parameters, and whether the NAS renderer's decoder handles them.
5. Latency added by each stage, and whether HomePods stay in sync through OwnTone's multi-room.
6. Whether the Pro-Ject Control app lists the NAS renderer (check with Bubble UPnP first).

## 6. Rules for the AI in this session

- Verify before asserting. Check FreeBSD package names, OwnTone config keys, and API endpoints in the docs or with the tools. Do not invent them.
- Keep `fake_turntable.py` standard-library only. Don't add pip dependencies unless the user agrees.
- Log every test in `RESULTS.md` with: date, device/renderer model, versions, the command run, and the observed result (pass/fail with details).
- When a test fails, record the exact error before changing anything. Change one thing at a time.
- Ask the user before any step that changes the NAS config or installs system packages on it.
- The user is in Europe/Zurich and writes in English.

## 7. Files in this folder

| File | Purpose |
|---|---|
| `STRATEGY.md` | This plan |
| `fake_turntable.py` | Mac-side stand-in for the T2 W: `--list`, play, `--stop` |
| `RESULTS.md` | Test log (create it during Phase 1) |

## 8. Quick start on the Mac

```bash
cd upnp-turntable-test
brew install ffmpeg
python3 fake_turntable.py --list
python3 fake_turntable.py --renderer "<name>"       # Ctrl+C to stop
python3 fake_turntable.py --renderer "<name>" --stop
```
