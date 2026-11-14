"""Writes wormhole icon variants (SVG) based on the Figma idea in icons/dev/wormhole.svg."""
import math, sys
from pathlib import Path

OUT = Path(sys.argv[1])
C = 512
GRAD = ('<linearGradient id="g" x1="0" y1="0" x2="512" y2="1024" gradientUnits="userSpaceOnUse">'
        '<stop stop-color="#D9C700"/><stop offset="1" stop-color="#00C6A8"/></linearGradient>')


def svg(body, bg=True):
    back = '<rect width="1024" height="1024" fill="black"/>' if bg else ""
    return ('<svg width="1024" height="1024" viewBox="0 0 1024 1024" fill="none" '
            f'xmlns="http://www.w3.org/2000/svg"><defs>{GRAD}</defs>{back}'
            f'<g fill="url(#g)" stroke="url(#g)">{body}</g></svg>\n')


def circ(cx, cy, r):
    return f"M{cx - r} {cy}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 {-2 * r} 0Z"


def ring(cx, cy, r_out, r_in, op=1.0):
    """Filled disc with a real hole (even-odd), so it also works on a transparent background."""
    return (f'<path fill-rule="evenodd" stroke="none" fill-opacity="{op}" '
            f'd="{circ(cx, cy, r_out)}{circ(cx, cy, r_in)}"/>')


def frame(inset=110, rx=74, w=16):
    s = 1024 - 2 * inset
    return f'<rect x="{inset}" y="{inset}" width="{s}" height="{s}" rx="{rx}" fill="none" stroke-width="{w}"/>'


def arm(px=786, py=238, ex=636, ey=700, pr=60, w=18):
    return (f'<line x1="{px}" y1="{py}" x2="{ex}" y2="{ey}" stroke-width="{w}" stroke-linecap="round"/>'
            f'<circle cx="{px}" cy="{py}" r="{pr}" stroke="none"/>')


def label(cx=C, cy=C, r=110, hole=20):
    return ring(cx, cy, r, hole)


variants = {}

# 1 Grooves: the disc as concentric rings that get brighter towards the centre.
variants["v1-grooves"] = svg(
    frame()
    + "".join(ring(C, C, r, r - 26, op) for r, op in ((274, .28), (232, .42), (190, .6), (148, .8)))
    + label() + arm())

# 2 Spiral: one groove spiralling into the spindle hole.
pts, turns, r0, r1 = [], 5.5, 262, 128
n = 900
for i in range(n + 1):
    t = i / n
    a = -math.pi / 2 + t * turns * 2 * math.pi
    r = r0 + (r1 - r0) * t
    pts.append(f"{C + r * math.cos(a):.1f} {C + r * math.sin(a):.1f}")
variants["v2-spiral"] = svg(
    frame()
    + ring(C, C, 274, 116, .18)
    + f'<path d="M{" L".join(pts)}" fill="none" stroke-width="10" stroke-linecap="round" stroke-opacity=".7"/>'
    + label(r=100) + arm())

# 3 Tunnel: stacked discs shifting towards the tonearm tip, like looking down a wormhole.
tun = ""
steps = ((512, 512, 274, .16), (528, 520, 226, .26), (544, 528, 180, .4),
         (558, 536, 136, .6), (570, 542, 94, 1))
for i, (x, y, r, op) in enumerate(steps):
    nx, ny, nr, _ = steps[i + 1] if i + 1 < len(steps) else (x, y, 18, 0)
    # each layer is a ring around the next one, so the opacities don't stack
    tun += (f'<path fill-rule="evenodd" stroke="none" fill-opacity="{op}" '
            f'd="{circ(x, y, r)}{circ(nx, ny, nr)}"/>')
variants["v3-tunnel"] = svg(frame() + tun + arm())

# 4 Full bleed: no frame, bigger record (iOS rounds the corners itself).
variants["v4-fullbleed"] = svg(
    ring(476, 548, 340, 136, .5) + label(476, 548, 136, 24)
    + arm(838, 186, 652, 760, 72, 22))

# 5 Broadcast: the record sends waves to the speakers (top right).
cx, cy = 430, 594
waves = ""
for r, op in ((310, 1), (380, .65), (450, .35)):
    a0, a1 = math.radians(-82), math.radians(-8)
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    waves += (f'<path d="M{x0:.1f} {y0:.1f}A{r} {r} 0 0 1 {x1:.1f} {y1:.1f}" fill="none" '
              f'stroke-width="28" stroke-linecap="round" stroke-opacity="{op}"/>')
variants["v5-broadcast"] = svg(
    frame() + ring(cx, cy, 236, 96, .5) + label(cx, cy, 96, 18) + waves)

# 6 Line: everything drawn with the frame's 16 px stroke; works as a monochrome glyph too.
variants["v6-line"] = svg(
    frame()
    + f'<g fill="none" stroke-width="16"><circle cx="{C}" cy="{C}" r="266"/>'
    f'<circle cx="{C}" cy="{C}" r="110"/><circle cx="{C}" cy="{C}" r="190" stroke-opacity=".45"/></g>'
    + f'<circle cx="{C}" cy="{C}" r="20" stroke="none"/>'
    + '<line x1="786" y1="238" x2="636" y2="700" stroke-width="16" stroke-linecap="round"/>'
    + '<circle cx="786" cy="238" r="52" fill="black" stroke-width="16"/>')

# 7 Portal: the record half sunk into a hole (the "wormhole"), arm on top.
variants["v7-portal"] = svg(
    frame()
    + '<clipPath id="above"><rect x="0" y="0" width="1024" height="660"/></clipPath>'
    + f'<g clip-path="url(#above)">{ring(C, 560, 274, 110, .5)}{label(C, 560, 110, 20)}</g>'
    + '<ellipse cx="512" cy="660" rx="330" ry="64" fill="none" stroke-width="16"/>'
    + '<ellipse cx="512" cy="660" rx="250" ry="34" stroke="none" fill-opacity=".35"/>'
    + arm(786, 300, 640, 640, 56, 18))

for name, text in variants.items():
    (OUT / f"{name}.svg").write_text(text)
print("\n".join(variants))
