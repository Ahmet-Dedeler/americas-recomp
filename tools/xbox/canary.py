"""Canary shaders: a few dozen known-good cases that must keep their recorded outcome before any corpus run starts.

Each harness bug so far (8-bit image format for atomics, fake user data, bindings from a stale bindings.py) moved the corpus numbers
and was found only after a long run. The canaries are picked to cover those paths, so the same class of mistake fails here in about
a minute instead:
  recompile canaries (driver + spirv-val, same code path as run_full.py)
    cs-atomic / ps-atomic   shaders with atomic image ops (need the atomic marking in the bindings and the driver's 32-bit formats)
    ps-bary                 valid only through the host barycentric extension path
    ps-f16                  16-bit interpolants: must THROW strict and be VALID with the opt-in (the default-off policy)
    cs / ps / vs / ms / tail plain valid shaders of each stage, and from the second entry run
  compare canaries (GPU run vs CPU reference, same code path as cpu_compare*.py)
    cmp-cs / cmp-cs-img / cmp-ps   byte-exact MATCHes, including image ones and ones that change memory

usage (from the build dir):
  python -I canary.py                 run all canaries, exit 1 if any outcome differs from canary.json
  python -I canary.py --select        re-pick canaries from the current results and rewrite canary.json (only after a reviewed run)
  python -I canary.py --binds-dir D   run the recompile canaries against another bindings directory (self-test, see XBOX_SHADERS.md)
run_full.py calls recompile_ok() before a corpus run; cpu_compare*.py call compare_ok().
"""
import concurrent.futures as cf, csv, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LIST = os.path.join(HERE, "canary.json")
M = r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
BUILD = r"E:/Programming/anyps5_src/build/"


def _rf():
    import run_full
    return run_full


def _recompile(c):
    rf = _rf()
    r = {"idx": c["idx"], "stage": c["stage"], "run": "canary"}
    return rf.one((r, c["relaxed"]))["status"]


def _compare(c):
    if c["kind"] == "cmp-ps":
        import cpu_compare_ps as P
        return P.one((c["idx"], 64))["outcome"]
    import cpu_compare as X
    return X.one(c["idx"])["outcome"]


def _check(cases, fn, workers, label):
    t = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:       # the work is in subprocesses (driver, exec) or short CPU runs
        got = list(ex.map(fn, cases))
    bad = [(c["kind"], c["idx"], "relaxed" if c.get("relaxed") else "", c["expect"], g) for c, g in zip(cases, got) if g != c["expect"]]
    print(f"canary {label}: {len(cases) - len(bad)} of {len(cases)} as recorded ({time.time() - t:.1f} s)", flush=True)
    if os.environ.get("CANARY_VERBOSE"):
        for c, g in zip(cases, got):
            print("   ", c["kind"], c["idx"], "relaxed" if c.get("relaxed") else "strict", g)
    for b in bad:
        print("  CHANGED %-10s %-7s %-7s expected %s, got %s" % b, flush=True)
    return not bad


def recompile_ok(workers=8, binds_dir=None):
    if binds_dir:
        _rf().BINDS = binds_dir.rstrip("/") + "/"
    cases = [c for c in json.load(open(LIST)) if not c["kind"].startswith("cmp")]
    return _check(cases, _recompile, workers, "recompile")


def compare_ok(workers=6):
    import run_real as R          # reads XBOX_EXEC once at import, so the executable is switched on the module
    cases = [c for c in json.load(open(LIST)) if c["kind"].startswith("cmp")]
    cs = [c for c in cases if c["kind"] != "cmp-ps"]
    ps = [c for c in cases if c["kind"] == "cmp-ps"]
    saved = R.EXEC
    R.EXEC = BUILD + "exec.exe"          # cpu_compare_ps.py uses its own exec_ps.exe
    ok = _check(cs, _compare, workers, "compare (compute)")
    ok = _check(ps, _compare, workers, "compare (pixel)") and ok
    R.EXEC = saved
    return ok


def select():
    """Pick canaries from the current results (smallest shaders first, so the suite stays fast)."""
    man = {r["idx"]: r for r in csv.DictReader(open(M + "manifest_full.csv"))}
    size = {i: int(r["bytes"]) for i, r in man.items()}
    res = {}
    for f in ("results_full.json", "results_final_gfx.json", "results_full_corpus.json"):
        for e in json.load(open(BUILD + f)):
            res[(e["idx"], bool(e.get("relaxed", False)))] = e["status"]
    binds = lambda i: open(M + f"binds_full/{i}.bind").read()
    atomic = lambda i: any(l.split()[-1][-1:] in "ab" and l.startswith("desc") and l.split()[-1].startswith("img") for l in binds(i).splitlines())
    ok = {"VALID", "VALID_BARY_EXT"}
    by = lambda st: sorted((i for i in man if man[i]["stage"] == st), key=lambda i: size[i])
    out = []

    def take(kind, ids, n, relaxed, expect, also=None):
        k = 0
        for i in ids:
            if k == n:
                break
            if res.get((i, relaxed)) == expect and (also is None or also(i)) and size[i] > 200:
                out.append({"kind": kind, "idx": i, "stage": man[i]["stage"], "relaxed": relaxed, "expect": expect})
                k += 1
    take("cs-atomic", by("CS"), 3, False, "VALID", atomic)
    take("cs", by("CS"), 2, False, "VALID")
    take("ps-atomic", by("PS"), 3, False, "VALID", atomic)
    take("ps-atomic", by("PS"), 1, True, "VALID", lambda i: atomic(i) and res.get((i, False)) == "THROW")
    take("ps-bary", by("PS"), 2, False, "VALID_BARY_EXT")
    f16 = [i for i in by("PS") if res.get((i, False)) == "THROW" and res.get((i, True)) == "VALID"]
    for i in f16[:2]:
        out.append({"kind": "ps-f16", "idx": i, "stage": "PS", "relaxed": False, "expect": "THROW"})
        out.append({"kind": "ps-f16", "idx": i, "stage": "PS", "relaxed": True, "expect": "VALID"})
    take("ps", by("PS"), 2, False, "VALID")
    take("vs", by("VS"), 2, False, "VALID")
    take("ms", by("?"), 3, False, "VALID")
    take("tail", [i for i in by("PS") + by("VS") if int(i) >= 100000], 2, False, "VALID")
    # compare canaries: from the committed comparison results
    cc = json.load(open(os.path.join(HERE, "..", "..", "docs", "xbox", "cpu_compare.json")))
    m = sorted((r for r in cc if r["outcome"] == "MATCH" and r["cpu_changed_memory"]), key=lambda r: size.get(r["idx"], 1 << 30))
    out += [{"kind": "cmp-cs", "idx": r["idx"], "expect": "MATCH"} for r in m[:4]]
    imgs = [r for r in m if "img" in binds(r["idx"])][:2]
    out += [{"kind": "cmp-cs-img", "idx": r["idx"], "expect": "MATCH"} for r in imgs]
    pc = json.load(open(os.path.join(HERE, "..", "..", "docs", "xbox", "cpu_compare_ps.json")))
    pm = sorted((r for r in pc if r["outcome"] == "MATCH"), key=lambda r: size.get(r["idx"], 1 << 30))
    out += [{"kind": "cmp-ps", "idx": r["idx"], "expect": "MATCH"} for r in pm[:3]]
    json.dump(out, open(LIST, "w"), indent=1)
    print(len(out), "canaries written to", LIST)


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--select" in a:
        select()
        sys.exit(0)
    bd = a[a.index("--binds-dir") + 1] if "--binds-dir" in a else None
    ok = recompile_ok(binds_dir=bd)
    if bd is None:
        ok = compare_ok() and ok
    sys.exit(0 if ok else 1)
