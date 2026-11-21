"""Static scan of the whole corpus for what a D3D12 back end would have to handle differently from Vulkan.

D3D12 has no raw GPU pointers, only descriptors, so shaders that dereference computed 64-bit addresses (FLAT/GLOBAL/SCRATCH
instructions, s_load from a base that is not a user-SGPR table) need an address -> resource mapping. D3D12 also cannot force 64 lanes
on hardware that runs 32, so shaders that depend on a 64-bit lane mask (wave64) matter on both APIs.

usage: python -I addr_scan.py <out.json>     (reads bins_full/, manifest_full.csv; one worker process per core)
Heuristics, not proofs: it looks at instruction names and a few encoding fields. The output holds per-stage counts and shader indices only.
"""
import concurrent.futures as cf, csv, collections, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
M = r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
sys.path.insert(1, r"E:/Programming/rpf9shaders/out_disasm")
import rdna2

SMEM_LOAD = ("s_load_dword", "s_buffer_load_dword")


def scan(idx):
    data = open(M + f"bins_full/{idx}.bin", "rb").read()
    ins, _ = rdna2.sweep(data, 0, len(data))
    f = collections.Counter()
    for i in ins:
        fmt, n = i.fmt.split("+")[0], i.name
        w0 = i.words[0]
        if fmt in ("FLAT", "GLOBAL", "SCRATCH") or n.startswith(("flat_", "global_", "scratch_")):
            f["flat" if not n.startswith("scratch_") else "scratch"] += 1          # computed 64-bit address in VGPRs
            if "atomic" in n:
                f["flat_atomic"] += 1
        if fmt == "SMEM" and n.startswith("s_load_dword"):
            f["s_load"] += 1
        # wave64 indicators: 64-bit scalar ops on exec, 64-bit lane masks consumed as a count, hi half of mbcnt
        if fmt in ("SOP1", "SOP2") and n.endswith("_b64"):
            dst = (w0 >> 16) & 0x7f if fmt == "SOP1" else (w0 >> 16) & 0x7f
            if dst in (126, 106) or "saveexec" in n or "wqm" in n:
                f["exec64"] += 1
        if "saveexec_b64" in n:
            f["exec64"] += 1
        if n in ("s_bcnt1_i32_b64", "s_bcnt0_i32_b64", "s_ff1_i32_b64", "s_flbit_i32_b64"):
            f["lanemask64_count"] += 1
        if n.startswith("v_mbcnt_hi"):
            f["mbcnt_hi"] += 1
        if n.startswith("v_readlane") or n.startswith("v_writelane"):
            f["lane_index"] += 1                      # arbitrary lane number: depends on the wave size
        if n.startswith(("ds_bpermute", "ds_permute")):
            f["lane_index"] += 1
        if n.startswith("ds_swizzle"):
            f["swizzle_quad" if (w0 >> 15) & 1 else "swizzle_wide"] += 1     # offset[15]=1: within a quad (derivatives); else bit-mask over 32 lanes
        if fmt in ("VOP1", "VOP2", "VOPC") and (w0 & 0x1ff) == 250 and len(i.words) > 1:
            f["dpp_quad" if ((i.words[1] >> 8) & 0x1ff) < 0x100 else "dpp_row"] += 1   # quad_perm vs row/wave shifts, rotates, mirrors
    # raw pointers hidden behind scalar loads: s_loads whose base the binding analysis could not resolve to a user table
    bind = open(M + f"binds_full/{idx}.bind").read().split("\n")
    f["child_nodes"] = sum(1 for l in bind if l.startswith("node") and " child " in l)
    return idx, dict(f)


def main():
    out = sys.argv[1]
    man = {r["idx"]: r["stage"] for r in csv.DictReader(open(M + "manifest_full.csv"))}
    ids = sorted(man, key=int)
    res = {}
    with cf.ProcessPoolExecutor(max(1, (os.cpu_count() or 4) - 2)) as ex:
        for idx, f in ex.map(scan, ids, chunksize=64):
            res[idx] = f
    names = {"PS": "pixel", "VS": "vertex", "CS": "compute", "?": "mesh", "GS": "geometry"}
    summary, lists = {}, collections.defaultdict(list)
    for st in sorted(set(man.values())):
        mine = [i for i in ids if man[i] == st]
        row = {"shaders": len(mine)}
        row["flat/global/scratch memory ops"] = sum(1 for i in mine if res[i].get("flat") or res[i].get("scratch"))
        row["64-bit exec / saveexec"] = sum(1 for i in mine if res[i].get("exec64"))
        row["64-bit lane-mask count"] = sum(1 for i in mine if res[i].get("lanemask64_count"))
        row["v_mbcnt_hi"] = sum(1 for i in mine if res[i].get("mbcnt_hi"))
        row["any wave64 indicator"] = sum(1 for i in mine if any(res[i].get(k) for k in ("exec64", "lanemask64_count", "mbcnt_hi")))
        row["lane-indexed (readlane/writelane/bpermute)"] = sum(1 for i in mine if res[i].get("lane_index"))
        row["quad-only (swizzle/DPP quad_perm: derivatives)"] = sum(1 for i in mine if res[i].get("swizzle_quad") or res[i].get("dpp_quad"))
        row["wide cross-lane (wide swizzle, DPP row ops)"] = sum(1 for i in mine if res[i].get("swizzle_wide") or res[i].get("dpp_row"))
        row["pointer-chased descriptor tables"] = sum(1 for i in mine if res[i].get("child_nodes"))
        summary[names.get(st, st)] = row
    json.dump({"summary": summary, "flat_ids": [i for i in ids if res[i].get("flat") or res[i].get("scratch")]}, open(out, "w"), indent=1)
    for st, row in summary.items():
        print(st, row)


if __name__ == "__main__":
    main()
