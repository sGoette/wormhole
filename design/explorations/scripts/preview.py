"""Builds dev/preview.html: every icon variant of all rounds on one page.

Icons with a background are <img> tags. Transparent variants are inlined so their mono parts
follow the panel's text colour (currentColor); ids get a unique prefix per copy, because the
variants reuse names like "g" and "m" for different gradients and masks.
"""
import itertools
import re
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
ROUNDS = [
    ("Round 1: variants of your idea", "Same palette and frame; no transparent versions.",
     ["wormhole", "v1-grooves", "v2-spiral", "v3-tunnel", "v4-fullbleed", "v5-broadcast",
      "v6-line", "v7-portal"]),
    ("Round 2: full bleed, grooves, tonearm, waves", "Yellow→teal, no frame.",
     ["w1-arm-bent", "w2-arm-straight", "w3-broadcast", "w4-dissolve", "w5-arm-waves"]),
    ("Round 3: wormhole symbols", "Mono record + one gradient element.",
     ["p1-portal", "p2-horizon", "p3-tunnel", "p4-well", "p5-bridge", "p6-ring", "p7-swirl"]),
]
counter = itertools.count()


def inline(name: str, cls: str) -> str:
    text = (DEV / f"{name}.svg").read_text().strip()
    p = f"u{next(counter)}-"
    text = re.sub(r'id="([^"]+)"', lambda m: f'id="{p}{m.group(1)}"', text)
    text = re.sub(r'url\(#([^)]+)\)', lambda m: f"url(#{p}{m.group(1)})", text)
    text = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{p}{m.group(1)}"', text)
    text = re.sub(r'^<svg width="1024" height="1024"', f'<svg class="{cls}" aria-hidden="true"',
                  text)
    return text


def card(name: str) -> str:
    transparent = (DEV / f"{name}-transparent.svg").exists()
    t = f"{name}-transparent" if transparent else name
    icon = f'<img src="{name}.svg" alt="{name}">'

    def row(theme: str) -> str:  # built per row: every inlined copy needs its own ids
        return (f'<div class="row {theme}"><img class="app" src="{name}.svg" alt="">'
                f'<img class="round" src="{name}.svg" alt="">'
                + inline(t, "f32") + inline(t, "f16")
                + f'<span class="head">{inline(t, "logo")}Wormhole</span></div>')
    big = (f'<div class="big">{icon}</div>'
           + (f'<div class="big light">{inline(t, "")}</div>'
              f'<div class="big dark">{inline(t, "")}</div>' if transparent else
              '<div class="big none">no transparent version</div><div class="big none"></div>'))
    title = "wormhole (your Figma idea)" if name == "wormhole" else name
    return (f'<article class="card"><h3>{title}</h3><div class="bigs">{big}</div>'
            + row("dark") + row("light") + "</article>")


sections = "".join(
    f'<section><h2>{title}</h2><p>{note}</p><div class="grid">'
    + "".join(card(n) for n in names) + "</div></section>"
    for title, note, names in ROUNDS)

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Icon variants</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; padding: 24px 16px 48px; font: 14px -apple-system, system-ui, sans-serif;
         background: #f2f2f7; color: #111; }}
  main {{ max-width: 1400px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 18px; margin: 32px 0 2px; }}
  section > p, header p {{ margin: 0 0 12px; color: #666; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 400px), 1fr));
           gap: 16px; }}
  .card {{ background: #fff; border-radius: 16px; padding: 14px; }}
  .card h3 {{ font-size: 14px; margin: 0 0 10px; }}
  .bigs {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }}
  .big {{ aspect-ratio: 1; border-radius: 8px; overflow: hidden; display: grid;
          place-items: center; }}
  .big img, .big svg {{ width: 100%; height: 100%; display: block; }}
  .big.light {{ background: #fff; color: #111; outline: 1px solid #e0e0e5; outline-offset: -1px; }}
  .big.dark {{ background: #1c1c1e; color: #fff; }}
  .big.none {{ background: #f2f2f7; color: #999; font-size: 12px; text-align: center;
               padding: 8px; }}
  .row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 8px;
          padding: 10px 12px; border-radius: 10px; }}
  .row.dark {{ background: #1c1c1e; color: #fff; }}
  .row.light {{ background: #fff; color: #111; outline: 1px solid #e0e0e5; outline-offset: -1px; }}
  .app {{ width: 60px; height: 60px; border-radius: 13.4px; }}
  .round {{ width: 60px; height: 60px; border-radius: 50%; }}
  .f32 {{ width: 32px; height: 32px; }}
  .f16 {{ width: 16px; height: 16px; }}
  .head {{ display: inline-flex; align-items: center; gap: 8px; font-weight: 700;
           font-size: 20px; margin-left: auto; }}
  .logo {{ width: 34px; height: 34px; }}
</style>
</head>
<body>
<main>
<header>
  <h1>wormhole icon variants</h1>
  <p>Each card: icon with background · transparent on white · transparent on dark; below,
    60 px with iOS corners, 60 px circle crop, 32 px, 16 px and the page header.</p>
</header>
{sections}
</main>
</body>
</html>
"""
(DEV / "preview.html").write_text(html)
print(f"{DEV / 'preview.html'}: {len(html) // 1024} KB")
