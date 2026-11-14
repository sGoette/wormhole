#!/usr/bin/env python3
"""Render every icon and artwork file from the two SVG masters in src/wormhole/frontend/icons/.

Mac only: uses macOS's `sips` to rasterize the SVGs. The server just serves the results.
Re-run after changing a master: python3 tools/make_icons.py
"""

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "src" / "wormhole" / "frontend"
ICONS = FRONTEND / "icons"
DARK, LIGHT = ICONS / "wormhole-dark.svg", ICONS / "wormhole-light.svg"

# (master, output file in icons/, size in px). iOS has no theme-dependent Home Screen icon for
# web apps, so the Home Screen, manifest, renderer and artwork files use the dark version.
PNGS = [
    (LIGHT, "favicon-light-32.png", 32),
    (DARK, "favicon-dark-32.png", 32),
    (DARK, "apple-touch-icon.png", 180),  # iOS rounds the corners itself
    (DARK, "icon-192.png", 192),  # web manifest
    (DARK, "icon-512.png", 512),
    (DARK, "renderer-48.png", 48),  # UPnP <iconList> (renderer.py)
    (DARK, "renderer-120.png", 120),
    (DARK, "renderer-256.png", 256),
]
# "Now playing" artwork that OwnTone sends to AirPlay devices: square, full 4K height.
# JPEG keeps it at a few hundred KB; OwnTone scales it down to what a device asks for.
ARTWORK = (DARK, "now-playing.jpg", 2160, 92)
FAVICON_ICO = (DARK, FRONTEND / "favicon.ico", (16, 32, 48))  # browsers ask for /favicon.ico


def sips(src: Path, out: Path, size: int, fmt: str = "png", quality: int = 0) -> None:
    cmd = ["sips", "-s", "format", fmt]
    if quality:
        cmd += ["-s", "formatOptions", str(quality)]
    cmd += ["-z", str(size), str(size), str(src), "--out", str(out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def ico(pngs: list) -> bytes:
    """An .ico holding PNG images (allowed since Windows Vista, read by all browsers)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for size, png in pngs:
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def main() -> int:
    if sys.platform != "darwin":
        print("make_icons.py needs macOS (sips)", file=sys.stderr)
        return 1
    for src, name, size in PNGS:
        sips(src, ICONS / name, size)
        print(f"icons/{name}  {size}x{size}")
    src, name, size, quality = ARTWORK
    sips(src, ICONS / name, size, "jpeg", quality)
    print(f"icons/{name}  {size}x{size} JPEG")
    src, out, sizes = FAVICON_ICO
    with tempfile.TemporaryDirectory() as tmp:
        pngs = []
        for size in sizes:
            png = Path(tmp) / f"{size}.png"
            sips(src, png, size)
            pngs.append((size, png.read_bytes()))
    out.write_bytes(ico(pngs))
    print(f"{out.name}  {', '.join(map(str, sizes))} px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
