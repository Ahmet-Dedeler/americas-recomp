"""Build docs/xbox/progress.svg (one cell per shader) from docs/xbox/results.json.
results.json: {"compute":{idx:"valid|fail"}, "pixel":{idx:"strict|relaxed|fail"}, "vertex":{...}, "mesh":{...}}"""
import json, sys, html
src = sys.argv[1] if len(sys.argv) > 1 else "docs/xbox/results.json"
out = sys.argv[2] if len(sys.argv) > 2 else "docs/xbox/progress.svg"
R = json.load(open(src))
COL = {"valid": "#2da44e", "strict": "#2da44e", "relaxed": "#d4a72c", "fail": "#cf222e", "standard": "#2da44e", "package": "#2da44e", "shim": "#d4a72c"}
ORDER = {"valid": 0, "strict": 0, "standard": 0, "package": 0, "relaxed": 1, "shim": 1, "fail": 2}
SECTIONS = [("compute", "Compute"), ("pixel", "Pixel"), ("vertex", "Vertex"), ("mesh", "Mesh"), ("platform", "Platform imports of the 16 package DLLs")]
CELL, GAP, W, PAD = 8, 2, 1000, 20
step = CELL + GAP
cols = (W - 2 * PAD) // step
y = PAD + 14
parts = []
for key, title in SECTIONS:
    items = sorted(R[key].values(), key=lambda s: ORDER[s])
    n = len(items)
    good = sum(1 for s in items if s in ("valid", "strict", "standard", "package"))
    rel = sum(1 for s in items if s in ("relaxed", "shim"))
    if key == "platform":
        label = f"{title}: {good} / {n} resolve on a PC as they are ({100*good/n:.1f}%), {rel} need a shim (written, load-tested)"
    else:
        label = f"{title}: {good} / {n} valid ({100*good/n:.1f}%)" + (f", +{rel} with an opt-in" if rel else "")
    parts.append(f'<text x="{PAD}" y="{y}" class="t">{html.escape(label)}</text>')
    y += 8
    for i, s in enumerate(items):
        parts.append(f'<rect x="{PAD+(i%cols)*step}" y="{y+(i//cols)*step}" width="{CELL}" height="{CELL}" rx="1.5" fill="{COL[s]}"/>')
    y += ((n + cols - 1) // cols) * step + 26
# rows with nothing measured yet: drawn as grey outlined bars so that missing work is as visible as finished work
for item in R.get("todo", []):
    parts.append(f'<text x="{PAD}" y="{y}" class="t">{html.escape(item["title"])}</text>')
    y += 8
    parts.append(f'<rect x="{PAD}" y="{y}" width="{W-2*PAD}" height="{CELL+4}" rx="3" fill="none" stroke="#484f58" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{PAD+8}" y="{y+CELL+1}" class="g">{html.escape(item["note"])}</text>')
    y += CELL + 4 + 22
legend = [("#2da44e", "shaders: valid SPIR-V (spirv-val); imports: resolve on a PC"), ("#d4a72c", "shaders: valid only with an opt-in; imports: Xbox-only, shimmed"), ("#cf222e", "fails"), ("#484f58", "not started / not measurable")]
x = PAD
for c, t in legend:
    if x + 30 + 6 * len(t) > W - PAD and x > PAD:    # wrap to a new line instead of running off the edge
        x = PAD
        y += 16
    parts.append(f'<rect x="{x}" y="{y-9}" width="10" height="10" rx="2" fill="{c}"/><text x="{x+15}" y="{y}" class="l">{t}</text>')
    x += 30 + 7 * len(t)
y += 14
parts.append(f'<text x="{PAD}" y="{y+6}" class="l">Shader cells are spirv-val only (execution results: docs/xbox/XBOX_SHADERS.md). Mesh config is a guess. Imports are static; the game executable is not included.</text>')
H = y + 22
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
       '<style>.t{font:600 13px system-ui,sans-serif;fill:#8b949e}.l{font:11px system-ui,sans-serif;fill:#8b949e}.g{font:11px system-ui,sans-serif;fill:#6e7681}</style>'
       f'<rect width="{W}" height="{H}" rx="8" fill="#0d1117"/>' + "".join(parts) + '</svg>')
open(out, "w", encoding="utf-8").write(svg)
print(out, len(svg))
