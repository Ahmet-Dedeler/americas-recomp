"""Run driver.exe over extracted Xbox shader code blobs and validate the SPIR-V with spirv-val.

    python tools/xbox/run_batch.py --bins DIR --driver build-xbox/driver.exe --out OUT [--waves 32 64]
                                   [--timeout 60] [--jobs 8] [--spirv-val PATH] [--ids 1 2 3]

DIR holds <idx>.bin files (code only: prologue stripped, cut after the last s_endpgm; see extract_shaders.py).
Writes OUT/results.json and prints a summary. A failure reason is the first exception only; later problems in
the same shader are hidden until it is fixed.
"""
import argparse, collections, concurrent.futures as cf, json, pathlib, re, shutil, subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bins", required=True)
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--waves", type=int, nargs="+", default=[32, 64])
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--spirv-val", default=shutil.which("spirv-val") or "spirv-val")
    ap.add_argument("--ids", nargs="*")
    a = ap.parse_args()
    a.driver = str(pathlib.Path(a.driver).resolve())
    bins = pathlib.Path(a.bins)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = a.ids or sorted((p.stem for p in bins.glob("*.bin")), key=lambda s: (len(s), s))

    def one(job):
        i, w = job
        spv = out / f"{i}.{w}.spv"
        try:
            p = subprocess.run([a.driver, str(bins / f"{i}.bin"), str(w), str(spv)], capture_output=True, text=True, timeout=a.timeout)
            line = (p.stdout.strip().splitlines() or [""])[-1]
            rc = p.returncode
        except subprocess.TimeoutExpired:
            line, rc = "TIMEOUT", -1
        r = dict(idx=i, wave=w)
        if line.startswith("OK"):
            v = subprocess.run([a.spirv_val, "--target-env", "vulkan1.3", str(spv)], capture_output=True, text=True)
            r.update(status="VALID" if v.returncode == 0 else "INVALID", detail=(v.stderr.strip().splitlines() or [""])[0][:200])
        elif line.startswith("THROW"):
            r.update(status="THROW", detail=line[6:].split(" RecompileRequest:")[0][:300])
        elif line == "TIMEOUT":
            r.update(status="TIMEOUT", detail="")
        else:
            r.update(status="CRASH", detail=line[:200] or f"rc={rc}")
        return r

    with cf.ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(one, [(i, w) for i in ids for w in a.waves]))
    (out / "results.json").write_text(json.dumps(res))
    for w in a.waves:
        sub = [r for r in res if r["wave"] == w]
        print(f"wave {w}:", dict(collections.Counter(r["status"] for r in sub)))
        reasons = collections.Counter(re.sub(r"\d+", "N", r["detail"])[:110] for r in sub if r["status"] not in ("VALID",))
        for k, v in reasons.most_common(8):
            print("   ", v, k)


if __name__ == "__main__":
    main()
