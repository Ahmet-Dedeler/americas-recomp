"""Execution tests: dispatch recompiled RDNA2 programs on the host GPU and compare with an independent expectation."""
import os, struct, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
import rdna_asm

EXEC = os.environ.get("XBOX_EXEC", r"E:/Programming/anyps5_src/build/exec.exe")

def run(program_bytes, regions, pokes=(), user=((4, 0),), groups=(1, 1, 1), wave=32, extra=()):
    """regions: {id: (size, initial_bytes|None)}; pokes: scenario lines. Returns {id: bytes} or raises."""
    d = tempfile.mkdtemp(prefix="exec_")
    code = os.path.join(d, "code.bin")
    open(code, "wb").write(program_bytes)
    lines = [f"wave {wave}", "groups %d %d %d" % groups, f"code {code}", f"out {d}/out"]
    for rid, (size, init) in regions.items():
        if init is not None:
            p = os.path.join(d, f"init{rid}.bin"); open(p, "wb").write(init)
            lines.append(f"region {rid} {size} init {p}")
        else:
            lines.append(f"region {rid} {size}")
    lines += list(pokes)
    lines += [f"user {s} {r}" for s, r in user]
    lines += list(extra)
    sc = os.path.join(d, "scenario.txt"); open(sc, "w").write("\n".join(lines) + "\n")
    p = subprocess.run([EXEC, sc], capture_output=True, text=True, timeout=300)
    out = p.stdout.strip()
    if "RAN" not in out:
        raise RuntimeError("exec failed: " + out[:700])
    return {rid: open(f"{d}/out/region{rid}.bin", "rb").read() for rid in regions}

def vsharp(srt_region, off, target, size):
    """scenario lines writing a raw buffer V# for region `target` at byte `off` of region `srt_region`"""
    return [f"ptrlo {srt_region} {off} {target}", f"ptrhi {srt_region} {off + 4} {target} 0",
            f"poke {srt_region} {off + 8} {size}", f"poke {srt_region} {off + 12} 0x30027fac"]

def test_store_lane_ids():
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.vop2('v_add_nc_u32', 2, 1000, 'v0')
    p.mubuf('buffer_store_dword', 2, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    r = run(p.bytes(), {0: (256, None), 1: (1024, None)}, vsharp(0, 0, 1, 1024))
    got = struct.unpack('<32I', r[1][:128])
    want = tuple(1000 + i for i in range(32))
    assert got == want, (got, want)

def test_readfirstlane_after_narrowed_write():
    # v2 = 1000 + lane. exec is narrowed to lanes > 3 and v2 = 7 there. exec is then narrowed to lanes > 1 (first active lane 2, which did not
    # take part in the write, so it still holds 1002). Hardware v_readfirstlane reads the FIRST ACTIVE lane: 1002.
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.vop2('v_add_nc_u32', 2, 1000, 'v0')
    p.sop1('s_mov_b32', 12, rdna_asm.EXEC_LO)        # s12 = full exec
    p.vopc('v_cmpx_lt_u32', 3, 'v0')                 # exec = lanes with 3 < lane
    p.vop1('v_mov_b32', 2, 7)                        # v2 = 7 on those lanes only
    p.sop1('s_mov_b32', rdna_asm.EXEC_LO, 's12')
    p.vopc('v_cmpx_lt_u32', 1, 'v0')                 # exec = lanes with 1 < lane
    p.vop1('v_readfirstlane_b32', 0, 'v2')           # s0 = v2 of the first active lane
    p.vop1('v_mov_b32', 3, 's0')
    p.mubuf('buffer_store_dword', 3, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    r = run(p.bytes(), {0: (256, None), 1: (1024, None)}, vsharp(0, 0, 1, 1024))
    got = struct.unpack('<32I', r[1][:128])
    want = tuple(1002 if i > 1 else 0 for i in range(32))
    assert got == want, ("got", got[:8], "want", want[:8])

def test_waterfall_loop():
    # Scalarization loop over 4 distinct per-lane values: every lane must see ITS OWN value in the loop body.
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.vop2('v_and_b32', 2, 3, 'v0')
    p.vop2('v_add_nc_u32', 2, 1000, 'v2')
    top = p.label()
    p.vop1('v_readfirstlane_b32', 0, 'v2')           # s0 = value of the first still-active lane
    p.vopc('v_cmp_eq_u32', 's0', 'v2')               # vcc = lanes holding the same value
    p.sop1('s_and_saveexec_b32', 13, rdna_asm.VCC_LO)
    p.vop1('v_mov_b32', 3, 's0')
    p.vop2('v_add_nc_u32', 3, 5, 'v3')               # body result = s0 + 5
    p.mubuf('buffer_store_dword', 3, 1, 8, 128, 0)
    p.sop2('s_xor_b32', rdna_asm.EXEC_LO, rdna_asm.EXEC_LO, 's13')
    p.branch_to('s_cbranch_execnz', top)
    p.sopp('s_endpgm')
    r = run(p.bytes(), {0: (256, None), 1: (1024, None)}, vsharp(0, 0, 1, 1024))
    got = struct.unpack('<32I', r[1][:128])
    want = tuple(1005 + (i & 3) for i in range(32))
    assert got == want, ("got", got[:8], "want", want[:8])

TESTS = [test_store_lane_ids, test_readfirstlane_after_narrowed_write, test_waterfall_loop]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t(); print("PASS", t.__name__)
        except Exception as e:
            bad += 1; print("FAIL", t.__name__, str(e)[:500])
    sys.exit(1 if bad else 0)
