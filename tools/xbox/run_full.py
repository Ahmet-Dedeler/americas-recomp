"""Run every shader of the full corpus (manifest_full.csv, bins_full/, binds_full/) through the recompiler and spirv-val.

usage (from the build dir):  python -I run_full.py <out.json> [--skip results_a.json ...] [--workers N] [--limit SEC] [--only STAGE] [--idx-file F] [--no-canary]

Before running it checks that every .bind was written by the current bindings.py (harness_guard.py) and that the canary shaders
still give their recorded outcomes (canary.py); either failing stops the run.

Same drivers and defaults as run_corpus_bind.py (compute, driver_heap.exe, variants 0/2/3 until one validates) and
run_graphics_final.py (PS/VS/MS, driver_fb.exe, PS both strict and with XBOX_ALLOW_UNMEASURED_F16_INTERP, barycentric extension
retry). Shaders listed in the --skip files (earlier results, by idx) are not run again. Results are written every 500 shaders, so a
killed run can be resumed by passing its own output as --skip. Stage '?' in the manifest is mesh (type 6/7), GS has no driver mode and
is recorded as SKIP."""
import subprocess, csv, sys, collections, concurrent.futures as cf, json, os, time
sys.path.insert(0, r"E:/Programming/rpf9shaders/out_disasm")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # harness_guard, canary (python -I does not add it)
import rdna2

M = r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL = r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
BUILD = r"E:/Programming/anyps5_src/build/"
LIMIT = 120                      # seconds per driver run
BINDS = M + "binds_full/"        # canary.py --binds-dir points this elsewhere for its self-test
GFX = {"PS": "ps", "VS": "vs", "?": "ms"}


def attrs(idx):
    d = open(M + f"bins_full/{idx}.bin", "rb").read()
    ins, _ = rdna2.sweep(d, 0, len(d))
    a, f = [], []
    for i in ins:
        if i.fmt.startswith("VINTRP"):
            a.append((i.words[0] >> 10) & 0x3F)
        elif i.name in ("v_interp_p1ll_f16", "v_interp_p1lv_f16", "v_interp_p2_f16"):
            x = i.words[1] & 0x3F
            a.append(x)
            f.append(x)
    return (max(a) + 1 if a else 0, sum(1 << x for x in set(f)))


def classify(cmd, spv, env):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=LIMIT, env=env)
        o = (p.stdout.strip().splitlines() or [""])[-1]
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return ("TIMEOUT", "")
    if o.startswith("OK"):
        v = subprocess.run([VAL, "--target-env", "vulkan1.3", spv], capture_output=True, text=True)
        return ("VALID", "") if v.returncode == 0 else ("INVALID", (v.stderr.strip().splitlines() or [""])[0][:150])
    if o.startswith("THROW"):
        return ("THROW", o[6:].split(" RecompileRequest:")[0][:250])
    return ("CRASH", o[:150] or f"rc={rc}")


def compute(idx):
    os.makedirs("spv_full", exist_ok=True)
    for v in (None, 2, 3):
        spv = f"spv_full/{idx}.{v}.spv"
        cmd = [BUILD + "driver_heap.exe", M + f"bins_full/{idx}.bin", "32", spv, BINDS + f"{idx}.bind"] + ([str(v)] if v else [])
        s, d = classify(cmd, spv, dict(os.environ))
        if os.path.exists(spv):
            os.remove(spv)
        if s == "VALID":
            return s, d, v
    return s, d, v


def graphics(idx, st, relaxed):
    env = dict(os.environ)
    if relaxed:
        env["XBOX_ALLOW_UNMEASURED_F16_INTERP"] = "1"

    def go(bary):
        os.makedirs("spv_full", exist_ok=True)
        spv = f"spv_full/{idx}.{int(relaxed)}{int(bary)}.spv"
        e = dict(env)
        if bary:
            e["XBOX_FRAGMENT_BARY"] = "1"
        cmd = [BUILD + "driver_fb.exe", M + f"bins_full/{idx}.bin", "32", spv, BINDS + f"{idx}.bind", "1", st]
        if st == "ps":
            n, mask = attrs(idx)
            cmd += ["2", str(n), "%x" % mask]
        r = classify(cmd, spv, e)
        if os.path.exists(spv):
            os.remove(spv)
        return r
    s, d = go(False)
    if s == "THROW" and "emulated barycentric" in d:
        s2, d2 = go(True)
        if s2 == "VALID":
            return "VALID_BARY_EXT", d2
    return s, d


def one(job):
    r, relaxed = job
    idx, stage = r["idx"], r["stage"]
    res = dict(idx=idx, stage=stage, run=r["run"], relaxed=relaxed)
    try:
        if stage == "CS":
            res["status"], res["detail"], res["variant"] = compute(idx)
        elif stage in GFX:
            res["status"], res["detail"] = graphics(idx, GFX[stage], relaxed)
        else:
            res["status"], res["detail"] = "SKIP", "no driver mode for " + stage
    except Exception as e:
        res["status"], res["detail"] = "HARNESS", repr(e)[:150]
    return res


def main():
    global LIMIT
    args = sys.argv[1:]
    OUT = args[0]
    skip_files = [args[i + 1] for i, a in enumerate(args) if a == "--skip"]
    WORKERS = int(args[args.index("--workers") + 1]) if "--workers" in args else 8
    LIMIT = int(args[args.index("--limit") + 1]) if "--limit" in args else 120
    ONLY = args[args.index("--only") + 1] if "--only" in args else None
    import harness_guard
    harness_guard.require_fresh_binds(BINDS)                 # refuse bindings made by another bindings.py
    if "--no-canary" not in args:
        import canary
        if not canary.recompile_ok(WORKERS):                 # known-good shaders must still pass before a corpus run
            sys.exit("canary failed: not starting the corpus run (see above; --no-canary overrides)")
    skip = set()
    for f in skip_files:
        for e in json.load(open(f)):
            skip.add((e["idx"], bool(e.get("relaxed", False))))
    IDX = set(open(args[args.index("--idx-file") + 1]).read().split()) if "--idx-file" in args else None
    rows = [r for r in csv.DictReader(open(M + "manifest_full.csv")) if (ONLY is None or r["stage"] == ONLY) and (IDX is None or r["idx"] in IDX)]
    jobs = [(r, x) for r in rows for x in ((False, True) if r["stage"] == "PS" else (False,)) if (r["idx"], x) not in skip]
    print(len(jobs), "jobs", flush=True)
    out, t0 = [], time.time()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for n, res in enumerate(ex.map(one, jobs)):
            out.append(res)
            if (n + 1) % 500 == 0:
                json.dump(out, open(OUT, "w"))
                print(n + 1, "done,", int(time.time() - t0), "s", flush=True)
    json.dump(out, open(OUT, "w"))
    for st in sorted({e["stage"] for e in out}):
        for x in (False, True):
            c = collections.Counter(e["status"] for e in out if e["stage"] == st and e["relaxed"] == x)
            if c:
                print(st, "relaxed" if x else "strict", dict(c))


if __name__ == "__main__":
    main()
