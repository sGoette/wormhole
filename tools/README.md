# Mac-only test tools

Never deployed to the NAS.

## fake_turntable.py

Stand-in for the Pro-Ject T2 W (STRATEGY.md §2). Standard library only; needs `ffmpeg`.

```bash
python3 tools/fake_turntable.py                    # web UI: http://localhost:8765 (also from iPhone)
python3 tools/fake_turntable.py --list             # every UPnP device, with "AVTransport: yes/no"
python3 tools/fake_turntable.py --renderer "Kitchen" --source "record.flac" --format flac
python3 tools/fake_turntable.py --renderer "Kitchen" --stop
```

- **Sources:** a 440 Hz test tone, every audio file in [media/](media/), or a path via `--source`.
- **Stream:** ffmpeg loops the file at real-time speed and encodes it to MP3 (320k, no Xing/ID3
  header) or FLAC (16-bit). 44.1 kHz stereo, no `Content-Length`, no range support, HTTP/1.0 close-
  delimited — a live stream like the turntable's. URL: `/live/<source>/stream.<mp3|flac>`.
  Each renderer connection gets its own ffmpeg, so a FLAC stream always starts with its header.
- **Web UI:** pick the speaker, source and format; Play / Pause / Stop. Shows the renderer's
  transport state (`GetTransportInfo`), which renderer connections are pulling the stream, and an
  event log with the exact UPnP errors — copy those into `RESULTS.md`.
- **Ctrl+C** (or SIGTERM) sends Stop to the active renderer before exiting.
- **Troubleshooting a renderer that rejects the stream:** try `--no-didl` (empty metadata),
  the other `--format`, and `-v` for HTTP request logging.

Not verified yet: the real T2 W's stream parameters and HTTP headers (STRATEGY.md §5.4).
The values above are reasonable defaults, to be compared once the device is here.
