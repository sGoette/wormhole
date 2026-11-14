"""Round 3: wormhole symbolism, v4-style record, mono (currentColor) + one gradient element.

Mono parts use currentColor: the icon version sets it (white on black / black on white); the
transparent version leaves it to the page (inline SVG follows the text colour, light or dark).
"""
import math, sys
from pathlib import Path

OUT = Path(sys.argv[1])
PALETTES = {  # name: stops (objectBoundingBox gradients, so transforms don't matter)
    "lime": ("#D9C700", "#00C6A8"),
    "dusk": ("#7B2FF7", "#F107A3", "#FF9F1C"),
    "ice": ("#00E0FF", "#3A47D5"),
}


def grads(p, pal):
    stops = PALETTES[pal]
    st = "".join(f'<stop offset="{i / (len(stops) - 1):.2f}" stop-color="{c}"/>'
                 for i, c in enumerate(stops))
    return (f'<linearGradient id="{p}l" x1="0" y1="0" x2="0.6" y2="1">{st}</linearGradient>'
            f'<linearGradient id="{p}v" x1="0" y1="0" x2="0" y2="1">{st}</linearGradient>'
            f'<radialGradient id="{p}r" cx=".5" cy=".5" r=".5">{st}</radialGradient>')


def svg(p, pal, body, bg, defs=""):
    if bg == "black":
        back, color = '<rect width="1024" height="1024" fill="#000"/>', ' color="#fff"'
    elif bg == "white":
        back, color = '<rect width="1024" height="1024" fill="#fff"/>', ' color="#111"'
    else:
        back, color = "", ""
    return ('<svg width="1024" height="1024" viewBox="0 0 1024 1024" fill="none" '
            f'xmlns="http://www.w3.org/2000/svg"{color}><defs>{grads(p, pal)}{defs}</defs>'
            f'{back}{body}</svg>\n')


def circ(cx, cy, r):
    return f"M{cx - r:.1f} {cy:.1f}a{r:.1f} {r:.1f} 0 1 0 {2 * r:.1f} 0a{r:.1f} {r:.1f} 0 1 0 {-2 * r:.1f} 0Z"


def ell(cx, cy, rx, ry):
    return (f"M{cx - rx:.1f} {cy:.1f}a{rx:.1f} {ry:.1f} 0 1 0 {2 * rx:.1f} 0"
            f"a{rx:.1f} {ry:.1f} 0 1 0 {-2 * rx:.1f} 0Z")


def hole(d_out, d_in, fill, op=1):
    return f'<path fill-rule="evenodd" d="{d_out}{d_in}" fill="{fill}" fill-opacity="{op}"/>'


M = "currentColor"
designs = {}

# p1 Portal: a horizontal gradient portal, the record half sunk into it (v7 + v4).
def p1(p):
    clip = f'<clipPath id="{p}c"><rect width="1024" height="660"/></clipPath>'
    body = (
        f'<path d="{ell(512, 660, 400, 104)}" fill="url(#{p}r)" fill-opacity=".35"/>'
        f'<path d="M112 660A400 104 0 0 1 912 660" stroke="url(#{p}l)" stroke-width="30"/>'
        f'<g clip-path="url(#{p}c)">'
        + hole(circ(512, 600, 330), circ(512, 600, 130), M, .45)
        + hole(circ(512, 600, 130), circ(512, 600, 24), M)
        + '</g>'
        f'<path d="M112 660A400 104 0 0 0 912 660" stroke="url(#{p}l)" stroke-width="30"/>')
    return body, clip
designs["p1-portal"] = (p1, "dusk", "black")

# p2 Event horizon: the label is a black hole with a glowing gradient accretion ring.
def p2(p):
    body = hole(circ(512, 512, 420), circ(512, 512, 196), M, .22)
    for r, op in ((380, .16), (330, .22), (280, .3), (236, .4)):
        body += f'<circle cx="512" cy="512" r="{r}" stroke="{M}" stroke-opacity="{op}" stroke-width="6"/>'
    body += hole(circ(512, 512, 180), circ(512, 512, 92), f"url(#{p}l)")
    body += f'<circle cx="512" cy="512" r="104" stroke="url(#{p}l)" stroke-width="4" stroke-opacity=".6"/>'
    # lensed disc crossing the hole, as in black-hole renderings
    body += (f'<path d="{ell(512, 512, 260, 34)}{ell(512, 512, 196, 18)}" fill-rule="evenodd" '
             f'fill="url(#{p}l)" fill-opacity=".9"/>')
    return body, ""
designs["p2-horizon"] = (p2, "lime", "black")

# p3 Tunnel: the grooves become a tunnel towards a gradient label at its far end.
def p3(p):
    steps = [(512, 512, 430), (546, 486, 340), (576, 462, 262), (600, 444, 196), (618, 430, 142)]
    body = ""
    for i, (x, y, r) in enumerate(steps):
        body += (f'<circle cx="{x}" cy="{y}" r="{r}" stroke="{M}" '
                 f'stroke-opacity="{1 - i * .17:.2f}" stroke-width="{22 - i * 3}"/>')
    body += hole(circ(632, 420, 98), circ(632, 420, 18), f"url(#{p}r)")
    return body, ""
designs["p3-tunnel"] = (p3, "ice", "white")

# p4 Gravity well: the record in perspective, its centre pulled down into a gradient funnel.
def p4(p):
    body = hole(ell(512, 360, 450, 150), ell(512, 372, 150, 50), M, .3)
    n = 9
    for k in range(n):
        t = k / (n - 1)
        rx = 150 * (1 - t) ** 1.5 + 18
        cy = 372 + 470 * t ** 0.8
        body += (f'<ellipse cx="512" cy="{cy:.1f}" rx="{rx:.1f}" ry="{rx / 3:.1f}" '
                 f'stroke="url(#{p}v)" stroke-width="{12 - k:.0f}"/>')
    # funnel walls
    pts_l, pts_r = [], []
    for k in range(41):
        t = k / 40
        rx = 150 * (1 - t) ** 1.5 + 18
        cy = 372 + 470 * t ** 0.8
        pts_l.append(f"{512 - rx:.1f} {cy:.1f}"); pts_r.append(f"{512 + rx:.1f} {cy:.1f}")
    for pts in (pts_l, pts_r):
        body += f'<path d="M{" L".join(pts)}" stroke="url(#{p}v)" stroke-width="10" stroke-linecap="round"/>'
    return body, ""
designs["p4-well"] = (p4, "dusk", "black")

# p5 Bridge: an Einstein-Rosen bridge. Record on top, throat in gradient, waves come out below.
def p5(p):
    top, bot, waist = 290, 740, 515
    body = hole(ell(512, top, 420, 120), ell(512, top, 130, 38), M, .35)
    throat = (f"M382 {top}C382 {top + 130} 470 {waist - 40} 470 {waist}"
              f"C470 {waist + 40} 382 {bot - 130} 382 {bot}"
              f"A130 38 0 0 0 642 {bot}C642 {bot - 130} 554 {waist + 40} 554 {waist}"
              f"C554 {waist - 40} 642 {top + 130} 642 {top}A130 38 0 0 1 382 {top}Z")
    body += f'<path d="{throat}" fill="url(#{p}v)"/>'
    body += f'<path d="{ell(512, top, 130, 38)}" fill="{M}"/>'
    body += f'<ellipse cx="512" cy="{top}" rx="22" ry="7" fill="#000" fill-opacity=".6"/>'
    for rx, op in ((210, .7), (300, .45), (390, .25)):
        body += (f'<path d="M{512 - rx} {bot}A{rx} {rx * .29:.0f} 0 0 0 {512 + rx} {bot}" '
                 f'stroke="{M}" stroke-opacity="{op}" stroke-width="18" stroke-linecap="round"/>')
    return body, ""
designs["p5-bridge"] = (p5, "dusk", "white")

# p6 Ring portal: the record slides through an upright gradient ring and is gone behind it.
def p6(p):
    clip = f'<clipPath id="{p}c"><rect width="640" height="1024"/></clipPath>'
    body = (f'<path d="M640 92A110 420 0 0 1 640 932" stroke="url(#{p}v)" stroke-width="34" '
            'stroke-opacity=".55"/>'
            f'<g clip-path="url(#{p}c)">'
            + hole(circ(420, 512, 360), circ(420, 512, 140), M, .45)
            + hole(circ(420, 512, 140), circ(420, 512, 26), M)
            + '</g>'
            f'<path d="M640 92A110 420 0 0 0 640 932" stroke="url(#{p}v)" stroke-width="34"/>')
    return body, clip
designs["p6-ring"] = (p6, "ice", "black")

# p7 Swirl: grooves spiral into the black-hole centre; the swirl is the gradient.
def p7(p):
    body = hole(circ(512, 512, 420), circ(512, 512, 200), M, .2)
    for arm in range(3):
        pts = []
        for i in range(121):
            t = i / 120
            a = arm * 2 * math.pi / 3 + t * 2.4 * math.pi
            r = 400 * (1 - t) + 70 * t
            pts.append(f"{512 + r * math.cos(a):.1f} {512 + r * math.sin(a):.1f}")
        body += (f'<path d="M{" L".join(pts)}" stroke="url(#{p}l)" stroke-width="30" '
                 'stroke-linecap="round"/>')
    body += f'<circle cx="512" cy="512" r="64" fill="#000"/>'
    body += f'<circle cx="512" cy="512" r="64" stroke="{M}" stroke-width="6" stroke-opacity=".8"/>'
    return body, ""
designs["p7-swirl"] = (p7, "lime", "black")

for name, (fn, pal, bg) in designs.items():
    p = name.split("-")[0]
    body, defs = fn(p)
    (OUT / f"{name}.svg").write_text(svg(p, pal, body, bg, defs))
    (OUT / f"{name}-transparent.svg").write_text(svg(p + "t", pal, fn(p + "t")[0], None,
                                                     fn(p + "t")[1]))
print(" ".join(designs))
