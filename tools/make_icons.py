#!/usr/bin/env python3
"""Draw the web UI's app icons (a record) as PNGs into src/wormhole/frontend/icons/.

Standard library only. Run again after changing the design: python3 tools/make_icons.py
"""

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "wormhole" / "frontend" / "icons"
SIZES = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512,
         "favicon-32.png": 32}

BG_TOP, BG_BOTTOM = (251, 146, 60), (194, 65, 12)  # the UI's accent, light to dark
VINYL, GROOVE, SHINE = (24, 24, 23), (44, 43, 41), (70, 68, 64)
LABEL, HOLE = (246, 245, 242), (24, 24, 23)
SAMPLES = 4  # per axis, for smooth edges


def colour_at(x: float, y: float) -> tuple:
    """Colour at (x, y) in a unit square; full-bleed so iOS can round the corners."""
    dx, dy = x - 0.5, y - 0.5
    r = math.hypot(dx, dy)
    if r < 0.025:
        return HOLE
    if r < 0.13:
        return LABEL
    if r < 0.40:
        # Grooves, plus a soft highlight on the upper left like light on vinyl.
        groove = GROOVE if int(r * 140) % 3 == 0 else VINYL
        angle = math.atan2(dy, dx)
        shine = max(0.0, math.cos(2 * (angle + math.pi * 0.75))) ** 6 * 0.5
        return tuple(round(g + (s - g) * shine) for g, s in zip(groove, SHINE))
    return tuple(round(t + (b - t) * y) for t, b in zip(BG_TOP, BG_BOTTOM))


def render(size: int) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter type: none
        for px in range(size):
            acc = [0, 0, 0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    c = colour_at((px + (sx + 0.5) / SAMPLES) / size,
                                  (py + (sy + 0.5) / SAMPLES) / size)
                    acc = [a + v for a, v in zip(acc, c)]
            row += bytes(round(a / SAMPLES ** 2) for a in acc)
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        (OUT / name).write_bytes(render(size))
        print(f"{OUT / name} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
