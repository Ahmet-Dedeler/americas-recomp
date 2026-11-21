"""Merge the per-shader results of the whole corpus into docs/xbox/results.json (the input of make_progress_svg.py).

usage (from the build dir): python -I make_full_results.py   [reads results_final_gfx.json, results_full_corpus.json and the compute
statuses already in docs/xbox/results.json; run_full.py wrote results_full_corpus.json]
Compute keeps the earlier per-shader statuses (they include the fake-memory variants) and adds the 42 compute shaders of the second
entry run (idx 100000+). Pixel: 'strict' if valid without the f16 opt-in, 'relaxed' if only with it, else 'fail'. Geometry shaders
(195) have no driver mode and are not in any section."""
import json, csv, os
M = r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
R = r"E:/Programming/anyps5_src/repo/docs/xbox/results.json"
r = json.load(open(R))
man = {x["idx"]: x["stage"] for x in csv.DictReader(open(M + "manifest_full.csv"))}
ok = {"VALID", "VALID_BARY_EXT"}
res = {}
for f in ("results_final_gfx.json", "results_full_corpus.json"):
    for e in json.load(open(f)):
        res[(e["idx"], bool(e.get("relaxed", False)))] = e["status"]
for e in json.load(open("results_full_corpus.json")):
    if man[e["idx"]] == "CS":
        r["compute"][e["idx"]] = "valid" if e["status"] in ok else "fail"
pixel, vertex, mesh = {}, {}, {}
for idx, st in man.items():
    if st == "PS":
        pixel[idx] = "strict" if res.get((idx, False)) == "VALID" else ("relaxed" if res.get((idx, False)) in ok or res.get((idx, True)) in ok else "fail")   # strict = no opt-in and no host extension
    elif st == "VS":
        vertex[idx] = "strict" if res.get((idx, False)) in ok else "fail"
    elif st == "?":
        mesh[idx] = "valid" if res.get((idx, False)) in ok else "fail"
r["pixel"], r["vertex"], r["mesh"] = pixel, vertex, mesh
json.dump(r, open(R, "w"))
for k in ("compute", "pixel", "vertex", "mesh"):
    import collections
    print(k, len(r[k]), dict(collections.Counter(r[k].values())))
