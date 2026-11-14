"""Contact sheet as one SVG: per variant icon / transparent on white / on dark, plus small sizes."""
import re, sys
from pathlib import Path

d = Path(sys.argv[1]); names = sys.argv[3:]; out = Path(sys.argv[2])

def nested(p, x, y, size):
    """The whole <svg> element (keeps its fill/color attributes), resized and placed."""
    t = p.read_text().strip()
    t = re.sub(r'^<svg width="1024" height="1024"',
               f'<svg x="{x}" y="{y}" width="{size}" height="{size}"', t)
    return t

def place(name, x, y, size, clip=None, color='#fff'):
    rx = {"app": size * 0.2237, "circle": size / 2}.get(clip, 0)
    cid = f"c{abs(hash((name, x, y)))}"
    return (f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{rx}"/></clipPath>'
            f'<g clip-path="url(#{cid})" color="{color}">{nested(d / name, x, y, size)}</g>')

W, row_h = 1240, 330
parts = [f'<rect width="{W}" height="{row_h * len(names) + 20}" fill="#e5e5ea"/>']
for i, n in enumerate(names):
    y = 20 + i * row_h
    parts.append(f'<text x="20" y="{y + 14}" font-family="Helvetica" font-size="18">{n}</text>')
    y += 24
    parts.append(place(f"{n}.svg", 20, y, 280))
    parts.append(f'<rect x="320" y="{y}" width="280" height="280" fill="#fff"/>')
    parts.append(place(f"{n}-transparent.svg", 320, y, 280, color="#111"))
    parts.append(f'<rect x="620" y="{y}" width="280" height="280" fill="#1c1c1e"/>')
    parts.append(place(f"{n}-transparent.svg", 620, y, 280))
    parts.append(f'<rect x="920" y="{y}" width="300" height="136" fill="#1c1c1e"/>')
    parts.append(f'<rect x="920" y="{y + 144}" width="300" height="136" fill="#fff"/>')
    for k, bg in enumerate((0, 144)):
        yy = y + bg + 20
        parts.append(place(f"{n}.svg", 935, yy, 60, "app"))
        parts.append(place(f"{n}.svg", 1010, yy, 60, "circle"))
        tc = "#fff" if k == 0 else "#111"
        parts.append(place(f"{n}-transparent.svg", 1085, yy + 14, 32, color=tc))
        parts.append(place(f"{n}-transparent.svg", 1132, yy + 22, 16, color=tc))
        parts.append(place(f"{n}-transparent.svg", 935, yy + 72, 36, color=tc))
        col = "#fff" if k == 0 else "#111"
        parts.append(f'<text x="978" y="{yy + 98}" font-family="Helvetica" font-weight="bold" '
                     f'font-size="22" fill="{col}">Wormhole</text>')
H = row_h * len(names) + 20
out.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
               f'viewBox="0 0 {W} {H}">{"".join(parts)}</svg>')
