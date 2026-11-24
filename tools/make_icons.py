#!/usr/bin/env python3
"""Render every icon and artwork file from the two SVG masters in src/wormhole/frontend/icons/.

Mac only: uses macOS's `sips` to rasterize the SVGs. The server just serves the results.
Re-run after changing a master: python3 tools/make_icons.py
"""

import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "src" / "wormhole" / "frontend"
ICONS = FRONTEND / "icons"
DARK, LIGHT = ICONS / "wormhole-dark.svg", ICONS / "wormhole-light.svg"

# (master, output file in icons/, size in px, transparent). iOS has no theme-dependent Home
# Screen icon for web apps, so the Home Screen, manifest, renderer and artwork files use the
# dark version.
# Transparent (no background square) where the place it is shown has its own background and
# its theme is known: the favicons follow the browser's theme, the renderer icons sit on the
# Pro-Ject app's dark list. Opaque where transparency goes wrong: iOS fills a transparent
# Home Screen icon with black, Android puts manifest icons on its own shapes.
PNGS = [
    (LIGHT, "favicon-light-32.png", 32, True),
    (DARK, "favicon-dark-32.png", 32, True),
    (DARK, "apple-touch-icon.png", 180, False),  # iOS rounds the corners itself
    (DARK, "icon-192.png", 192, False),  # web manifest
    (DARK, "icon-512.png", 512, False),
    (DARK, "renderer-48.png", 48, True),  # UPnP <iconList> (renderer.py)
    (DARK, "renderer-120.png", 120, True),
    (DARK, "renderer-256.png", 256, True),
]
# The masters' full-size background square; removed for the transparent files.
BACKGROUND = re.compile(r'<rect width="1024" height="1024" fill="[^"]*"/>')
# "Now playing" artwork that OwnTone sends to AirPlay devices: square, full 4K height.
# JPEG keeps it at a few hundred KB; OwnTone scales it down to what a device asks for.
ARTWORK = (DARK, "now-playing.jpg", 2160, 92)
# Browsers ask for /favicon.ico; it doesn't know the theme, so it keeps its background.
FAVICON_ICO = (DARK, FRONTEND / "favicon.ico", (16, 32, 48))


def sips(src: Path, out: Path, size: int, fmt: str = "png", quality: int = 0) -> None:
    cmd = ["sips", "-s", "format", fmt]
    if quality:
        cmd += ["-s", "formatOptions", str(quality)]
    cmd += ["-z", str(size), str(size), str(src), "--out", str(out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def without_background(master: Path, tmp: Path) -> Path:
    """A copy of the master without its background square."""
    text, count = BACKGROUND.subn("", master.read_text())
    if count != 1:
        raise SystemExit(f"{master.name}: expected one 1024x1024 background <rect>, "
                         f"found {count}")
    out = tmp / f"{master.stem}-transparent.svg"
    out.write_text(text)
    return out


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
    with tempfile.TemporaryDirectory() as tmp:
        for src, name, size, transparent in PNGS:
            sips(without_background(src, Path(tmp)) if transparent else src, ICONS / name, size)
            print(f"icons/{name}  {size}x{size}{'  transparent' if transparent else ''}")
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
