import argparse
import collections
import concurrent.futures as cf
import csv
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time


def parse_dxbc_gs_params(path):
    data = open(path, "rb").read()
    if len(data) < 32 or data[:4] != b"DXBC":
        return []
    nchunks = struct.unpack_from("<I", data, 28)[0]
    in_cnt = 0
    out_cnt = 0
    in_prim = 3
    out_topo = 3
    max_verts = 3
    for i in range(nchunks):
        off = struct.unpack_from("<I", data, 32 + 4 * i)[0]
        tag = data[off:off + 4]
        sz = struct.unpack_from("<I", data, off + 4)[0]
        payload = data[off + 8:off + 8 + sz]
        if tag in (b"ISG1", b"ISGN") and len(payload) >= 4:
            in_cnt = struct.unpack_from("<I", payload, 0)[0]
        elif tag in (b"OSG1", b"OSGN") and len(payload) >= 4:
            out_cnt = struct.unpack_from("<I", payload, 0)[0]
        elif tag == b"PSV0" and len(payload) >= 36:
            words = struct.unpack_from("<8I", payload, 4)
            in_prim = words[0]
            dx_topo = words[1]
            out_topo = 1 if dx_topo == 1 else 2 if dx_topo == 3 else 3
            max_verts = words[6] >> 16
    if in_cnt == 0 or out_cnt == 0 or max_verts == 0:
        return []
    return [
        str(in_prim),
        str(out_topo),
        str(max_verts),
        str(in_cnt * 4),
        str(out_cnt * 4),
        str(in_cnt),
        str(out_cnt),
    ]


def run_one(args):
    row, cfg = args
    idx = row["idx"]
    spv = os.path.join(cfg["tmpdir"], f"{idx}.spv")
    cmd = [
        cfg["driver"],
        os.path.join(cfg["bins"], f"{idx}.bin"),
        str(cfg["wave"]),
        spv,
        os.path.join(cfg["binds"], f"{idx}.bind"),
        str(cfg["variant"]),
        "gs",
    ]
    if cfg["dxbc"]:
        dxbc_path = os.path.join(cfg["dxbc"], f"{idx}.dxbc")
        if os.path.exists(dxbc_path):
            cmd.extend(parse_dxbc_gs_params(dxbc_path))
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg["limit"])
        lines = proc.stdout.strip().splitlines()
        out = lines[-1] if lines else ""
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        out, rc = "TIMEOUT", -1
    res = {
        "idx": idx,
        "stage": row.get("stage", "GS"),
        "name": row.get("name", ""),
        "secs": round(time.time() - t0, 3),
    }
    if out.startswith("OK"):
        res["words"] = int(out.split()[1]) if len(out.split()) > 1 else 0
        val = subprocess.run([cfg["val"], "--target-env", "vulkan1.3", spv], capture_output=True, text=True)
        if val.returncode == 0:
            res["status"] = "VALID"
            res["detail"] = ""
        else:
            err_lines = val.stderr.strip().splitlines()
            res["status"] = "INVALID"
            res["detail"] = (err_lines[0] if err_lines else "")[:200]
    elif out.startswith("THROW"):
        res["status"] = "THROW"
        res["detail"] = out[6:].split(" RecompileRequest:")[0][:300]
    else:
        res["status"] = "TIMEOUT" if out == "TIMEOUT" else "CRASH"
        res["detail"] = out[:200] or f"rc={rc}"
    try:
        os.remove(spv)
    except OSError:
        pass
    return res


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", required=True)
    parser.add_argument("--spirv-val", default=shutil.which("spirv-val") or "spirv-val")
    parser.add_argument("--bins", required=True)
    parser.add_argument("--binds", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dxbc", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--wave", type=int, default=64)
    parser.add_argument("--variant", type=int, default=1)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    rows = [r for r in csv.DictReader(open(args.manifest)) if r.get("stage") == "GS"]
    tmpdir = tempfile.mkdtemp(prefix="xbox_gs_spv_")
    cfg = {
        "driver": args.driver,
        "val": args.spirv_val,
        "bins": args.bins,
        "binds": args.binds,
        "dxbc": args.dxbc,
        "wave": args.wave,
        "variant": args.variant,
        "limit": args.limit,
        "tmpdir": tmpdir,
    }
    t0 = time.time()
    try:
        with cf.ThreadPoolExecutor(args.workers) as ex:
            results = list(ex.map(run_one, [(r, cfg) for r in rows]))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    elapsed = round(time.time() - t0, 2)
    counts = dict(collections.Counter(r["status"] for r in results))
    print(f"GS wave={args.wave} total={len(results)} elapsed={elapsed}s counts={counts}")
    failures = collections.Counter((r["status"], r["detail"]) for r in results if r["status"] != "VALID")
    for (status, detail), cnt in failures.most_common(20):
        print(f"  {cnt:4d}  {status}: {detail}")
    if args.out:
        json.dump(results, open(args.out, "w"), indent=2)


if __name__ == "__main__":
    main()
