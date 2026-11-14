"""Round 2: full bleed, v1 grooves, broadcast waves, a real tonearm; each also transparent."""
import math, sys
from pathlib import Path

OUT = Path(sys.argv[1])
GRAD = ('<linearGradient id="g" x1="0" y1="0" x2="512" y2="1024" gradientUnits="userSpaceOnUse">'
        '<stop stop-color="#D9C700"/><stop offset="1" stop-color="#00C6A8"/></linearGradient>')


def svg(body, bg):
    back = '<rect width="1024" height="1024" fill="black"/>' if bg else ""
    return ('<svg width="1024" height="1024" viewBox="0 0 1024 1024" fill="none" '
            f'xmlns="http://www.w3.org/2000/svg"><defs>{GRAD}'
            # shapes form a mask and one gradient fills it (as in the Figma export), so
            # transformed parts like the tonearm get the same gradient as the rest
            f'<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
            f'<g fill="white" stroke="white">{body}</g></mask></defs>{back}'
            '<rect width="1024" height="1024" fill="url(#g)" mask="url(#m)"/></svg>\n')


def circ(cx, cy, r):
    return f"M{cx - r:.1f} {cy:.1f}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 {-2 * r} 0Z"


def ring(cx, cy, r_out, r_in, op=1.0):
    return (f'<path fill-rule="evenodd" stroke="none" fill-opacity="{op}" '
            f'd="{circ(cx, cy, r_out)}{circ(cx, cy, r_in)}"/>')


def record(cx, cy, r, label, n=4, hole=None):
    """v1 grooves scaled to radius r: n rings getting brighter inwards, then the label."""
    step = (r - label) / n
    width = step * 0.62
    out = ""
    for i in range(n):
        ro = r - i * step
        op = 0.28 + 0.52 * i / max(n - 1, 1)
        out += ring(cx, cy, ro, ro - width, round(op, 2))
    return out + ring(cx, cy, label, hole or label * 0.18)


def arc(cx, cy, r, a0, a1, w, op):
    a0, a1 = math.radians(a0), math.radians(a1)
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    large = 1 if (a1 - a0) % (2 * math.pi) > math.pi else 0
    return (f'<path d="M{x0:.1f} {y0:.1f}A{r} {r} 0 {large} 1 {x1:.1f} {y1:.1f}" fill="none" '
            f'stroke-width="{w}" stroke-linecap="round" stroke-opacity="{op}"/>')


def waves(cx, cy, radii, a0, a1, w):
    ops = [1, .6, .3, .15]
    return "".join(arc(cx, cy, r, a0, a1, w, ops[i]) for i, r in enumerate(radii))


def arm_bent(x, y, rot, s=1.0):
    """Pivot bearing (ring) with counterweight behind it, S-bent arm, angled headshell.
    Local coordinates: pivot at 0,0, arm hanging down."""
    return (f'<g transform="translate({x} {y}) rotate({rot}) scale({s})">'
            '<rect x="-36" y="-138" width="72" height="72" rx="18" stroke="none"/>'
            '<line x1="0" y1="-80" x2="0" y2="-40" stroke-width="26"/>'
            + ring(0, 0, 54, 24)
            + '<path d="M0 54 L0 330 Q0 380 -36 410" fill="none" stroke-width="26" '
            'stroke-linecap="round"/>'
            '<g transform="translate(-50 424) rotate(42)">'
            '<rect x="-30" y="-34" width="60" height="84" rx="12" stroke="none"/></g></g>')


def arm_straight(x, y, rot, s=1.0):
    """Straight arm with an offset headshell (like most belt-drive decks)."""
    return (f'<g transform="translate({x} {y}) rotate({rot}) scale({s})">'
            '<rect x="-36" y="-138" width="72" height="72" rx="18" stroke="none"/>'
            '<line x1="0" y1="-80" x2="0" y2="-40" stroke-width="26"/>'
            + ring(0, 0, 54, 24)
            + '<line x1="0" y1="54" x2="0" y2="392" stroke-width="26" stroke-linecap="round"/>'
            '<g transform="translate(-8 420) rotate(28)">'
            '<rect x="-30" y="-36" width="60" height="86" rx="12" stroke="none"/></g></g>')


designs = {
    # grooves + bent arm
    "w1-arm-bent": lambda: record(468, 560, 330, 124) + arm_bent(778, 232, 16, 1.0),
    # grooves + straight arm
    "w2-arm-straight": lambda: record(468, 560, 330, 124) + arm_straight(778, 232, 16, 1.0),
    # grooves + broadcast, no arm
    "w3-broadcast": lambda: record(420, 604, 250, 94)
        + waves(420, 604, (320, 396, 472), -84, -6, 30),
    # grooves dissolving into waves: the outer grooves continue as open arcs
    "w4-dissolve": lambda: record(440, 584, 220, 90, n=3)
        + "".join(arc(440, 584, r, a0, a1, 32, op) for r, a0, a1, op in
                  ((270, -150, 60, .22), (330, -120, 30, .16), (390, -95, 5, .1))),
    # grooves + small bent arm + waves to the top left
    "w5-arm-waves": lambda: record(548, 584, 290, 110)
        + waves(548, 584, (346, 410), -170, -112, 28) + arm_bent(820, 290, 18, 0.8),
}

for name, make in designs.items():
    body = make()
    (OUT / f"{name}.svg").write_text(svg(body, True))
    (OUT / f"{name}-transparent.svg").write_text(svg(body, False))
print("\n".join(designs))
