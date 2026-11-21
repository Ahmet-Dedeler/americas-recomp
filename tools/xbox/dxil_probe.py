"""Feasibility probe: can Mesa's spirv_to_dxil turn our recompiled SPIR-V into DXIL (D3D12 back end instead of Vulkan)?

For a sample of shaders per stage that our recompiler already turns into valid SPIR-V, regenerate the SPIR-V with the same driver
command run_full.py uses, run spirv2dxil (from a Mesa build, -Dspirv-to-dxil=true) with its own DXIL validation (dxil.dll), and record
the outcome and the first error line. Mesh shaders are not attempted: spirv_to_dxil has no mesh stage.

usage (from the build dir): python -I dxil_probe.py <out.json> [--per-stage N] [--seed S]
env: SPIRV2DXIL (path to spirv2dxil.exe), DXIL_DLL_DIR (folder holding dxil.dll)
Output holds shader indices and outcomes only (no shader data).
"""
import collections, concurrent.futures as cf, csv, json, os, random, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
M = r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
BUILD = r"E:/Programming/anyps5_src/build/"
EXE = os.environ.get("SPIRV2DXIL", r"E:/Programming/anyps5_src/mesa/bld/src/microsoft/spirv_to_dxil/spirv2dxil.exe")
DXIL_DIR = os.environ.get("DXIL_DLL_DIR", r"C:/Program Files (x86)/Windows Kits/10/bin/10.0.26100.0/x64")
SM = os.environ.get("DXIL_SM", "6")      # -m N = shader model 6.N (6.6 for ResourceDescriptorHeap / CreateHandleFromHeap)
VALVER = os.environ.get("DXIL_VALVER", "8")  # -x N = DXIL validator 1.N; the default (1.4) rejects shader model 6.6 opcodes
STAGE = {"PS": "fragment", "VS": "vertex", "CS": "compute"}


def make_spv(idx, stage, spv):
    import run_full as rf
    if stage == "CS":
        for v in (None, 2, 3):
            cmd = [BUILD + "driver_heap.exe", M + f"bins_full/{idx}.bin", "32", spv, rf.BINDS + f"{idx}.bind"] + ([str(v)] if v else [])
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if (p.stdout.strip().splitlines() or [""])[-1].startswith("OK"):
                return True
        return False
    st = stage.lower()
    cmd = [rf.DRIVER_GFX, M + f"bins_full/{idx}.bin", "32", spv, rf.BINDS + f"{idx}.bind", "1", st]
    if st == "ps":
        n, mask = rf.attrs(idx)
        cmd += ["2", str(n), "%x" % mask]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return (p.stdout.strip().splitlines() or [""])[-1].startswith("OK")


def probe(job):
    idx, stage = job
    os.makedirs("dxil_probe_tmp", exist_ok=True)
    spv, dxil = f"dxil_probe_tmp/{idx}.spv", f"dxil_probe_tmp/{idx}.dxil"
    try:
        if not make_spv(idx, stage, spv):
            return idx, stage, "NO_SPV", ""
        env = dict(os.environ, PATH=DXIL_DIR + os.pathsep + os.environ["PATH"])
        p = subprocess.run([EXE, "-s", STAGE[stage], "-e", "main", "-m", SM, "-x", VALVER, "-v", "-o", dxil, spv], capture_output=True, text=True, timeout=120, env=env)
        err = (p.stderr + p.stdout).strip()
        if p.returncode == 0 and os.path.exists(dxil):
            return idx, stage, "DXIL_VALID", ""
        first = [l for l in err.splitlines() if l.strip()]
        return idx, stage, "FAIL", (first[0] if first else f"rc={p.returncode}")[:200]
    except subprocess.TimeoutExpired:
        return idx, stage, "TIMEOUT", ""
    finally:
        for f in (spv, dxil):
            if os.path.exists(f):
                os.remove(f)


def main():
    a = sys.argv[1:]
    out = a[0]
    n = int(a[a.index("--per-stage") + 1]) if "--per-stage" in a else 60
    random.seed(int(a[a.index("--seed") + 1]) if "--seed" in a else 1)
    man = {r["idx"]: r["stage"] for r in csv.DictReader(open(M + "manifest_full.csv"))}
    valid = set()
    for f in ("results_full.json", "results_full_corpus.json", "results_final_gfx.json"):
        for e in json.load(open(BUILD + f)):
            if e["status"] == "VALID" and not e.get("relaxed"):
                valid.add(e["idx"])
    flat = set(json.load(open(BUILD + "addr_scan.json"))["flat_ids"])
    jobs = []
    for st in STAGE:
        pool = [i for i in man if man[i] == st and i in valid]
        fl = [i for i in pool if i in flat]
        plain = [i for i in pool if i not in flat]
        pick = random.sample(plain, min(n, len(plain))) + random.sample(fl, min(max(n // 4, 5), len(fl)))
        jobs += [(i, st) for i in pick]
    res = []
    with cf.ProcessPoolExecutor(max(1, (os.cpu_count() or 4) - 2)) as ex:
        for idx, st, s, d in ex.map(probe, jobs):
            res.append({"idx": idx, "stage": st, "uses_raw_pointers": idx in flat, "status": s, "detail": d})
    json.dump(res, open(out, "w"), indent=1)
    tab = collections.Counter((r["stage"], r["uses_raw_pointers"], r["status"]) for r in res)
    for k in sorted(tab):
        print(k, tab[k])
    why = collections.Counter((r["stage"], r["detail"][:90]) for r in res if r["status"] == "FAIL")
    for k, v in why.most_common(15):
        print(v, k)


if __name__ == "__main__":
    main()
