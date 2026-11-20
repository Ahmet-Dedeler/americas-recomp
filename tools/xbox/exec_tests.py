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
    result = {rid: open(f"{d}/out/region{rid}.bin", "rb").read() for rid in regions}
    result['out'] = f"{d}/out"
    return result

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

def _run_image_select(selector):
    # SRT: T# A at 0, T# B at 32, selector at 64. The image T# is loaded into the same SGPRs on two paths and stored through after the join,
    # so the recompiler has to split the store over the descriptor Phi.
    p = rdna_asm.Program()
    p.smem('s_load_dword', 0, 4, 64)
    p.sopp('s_waitcnt', 0)
    p.sopc('s_cmp_eq_u32', 's0', 0)
    to_a = p.branch_fwd('s_cbranch_scc1')
    p.smem('s_load_dwordx8', 8, 4, 32)               # selector != 0: image B
    to_join = p.branch_fwd('s_branch')
    p.patch_to_here(to_a)
    p.smem('s_load_dwordx8', 8, 4, 0)                # selector == 0: image A
    p.patch_to_here(to_join)
    p.sopp('s_waitcnt', 0)
    p.vop1('v_cvt_f32_u32', 4, 'v0')                 # r = lane
    p.vop1('v_mov_b32', 5, ('lit', 0x40000000))      # g = 2.0
    p.vop1('v_mov_b32', 6, ('lit', 0x40600000))      # b = 3.5
    p.vop1('v_mov_b32', 7, ('lit', 0x3f800000))      # a = 1.0
    p.vop1('v_mov_b32', 1, 0)                        # y = 0
    p.mimg('image_store', 4, 0, 8)
    p.sopp('s_endpgm')
    pokes = ["image 1 32 1 clear -1 -1 -1 -1", "image 2 32 1 clear -1 -1 -1 -1", "timg 0 0 1", "timg 0 32 2", f"poke 0 64 {selector}"]
    r = run(p.bytes(), {0: (256, None)}, [], extra=pokes)
    return r

def _image_pixels(out_dir, image_id):
    return struct.unpack('<%df' % (32 * 4), open(os.path.join(out_dir, f"image{image_id}.bin"), 'rb').read())

def test_image_store_phi_split():
    for selector, written, untouched in ((0, 1, 2), (1, 2, 1)):
        r = _run_image_select(selector)
        d = os.path.dirname(r['dir']) if 'dir' in r else None
        pixels = {i: _image_pixels(r['out'], i) for i in (1, 2)}
        want = tuple(v for lane in range(32) for v in (float(lane), 2.0, 3.5, 1.0))
        assert pixels[written] == want, ("selector", selector, "image", written, pixels[written][:8])
        assert pixels[untouched] == tuple([-1.0] * 128), ("selector", selector, "image", untouched, "was written", pixels[untouched][:8])

def _group_prologue(p, threads):
    # s[4:5] = SRT pointer, 6 user SGPRs, so the workgroup id x is s6. v2 = group id * threads + local id (global linear id)
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop1('v_mov_b32', 2, 's6')
    p.vop2('v_mul_u32_u24', 2, ('lit', threads), 'v2')
    p.vop2('v_add_nc_u32', 2, 'v0', 'v2')
    p.vop2('v_lshlrev_b32', 1, 2, 'v2')

def test_multiwave_multigroup_global_id(wave=32):
    threads, groups = 128, 4          # four waves (wave32) or two (wave64) per group, four groups
    p = rdna_asm.Program()
    _group_prologue(p, threads)
    p.vop2('v_mul_u32_u24', 3, ('lit', 3), 'v2')
    p.vop2('v_add_nc_u32', 3, 1, 'v3')                # value = 3 * gid + 1
    p.mubuf('buffer_store_dword', 3, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    n = threads * groups
    r = run(p.bytes(), {0: (256, None), 1: (n * 4, None)}, vsharp(0, 0, 1, n * 4), groups=(groups, 1, 1), wave=wave,
            extra=[f"threads {threads} 1 1", "userdwords 6"])
    got = struct.unpack('<%dI' % n, r[1])
    assert got == tuple(3 * i + 1 for i in range(n)), [i for i in range(n) if got[i] != 3 * i + 1][:8]

def test_lds_reverse_across_waves(wave=32):
    # every thread writes lds[tid] then reads lds[127 - tid] after a barrier: wrong unless the barrier covers all four waves
    threads, groups = 128, 3
    p = rdna_asm.Program()
    _group_prologue(p, threads)
    p.vop2('v_lshlrev_b32', 4, 2, 'v0')               # v4 = local id * 4
    p.vop2('v_add_nc_u32', 5, 5000, 'v2')             # v5 = gid + 5000
    p.ds('ds_write_b32', 0, 4, 5)
    p.sopp('s_waitcnt', 0)
    p.sopp('s_barrier')
    p.vop2('v_sub_nc_u32', 6, ('lit', 127), 'v0')     # 127 - local id
    p.vop2('v_lshlrev_b32', 6, 2, 'v6')
    p.ds('ds_read_b32', 7, 6)
    p.sopp('s_waitcnt', 0)
    p.mubuf('buffer_store_dword', 7, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    n = threads * groups
    r = run(p.bytes(), {0: (256, None), 1: (n * 4, None)}, vsharp(0, 0, 1, n * 4), groups=(groups, 1, 1), wave=wave,
            extra=[f"threads {threads} 1 1", "lds 128", "userdwords 6"])
    got = struct.unpack('<%dI' % n, r[1])
    want = tuple(5000 + (g * threads + (threads - 1 - t)) for g in range(groups) for t in range(threads))
    assert got == want, [(i, got[i], want[i]) for i in range(n) if got[i] != want[i]][:6]

def test_multiwave_wave64():
    test_multiwave_multigroup_global_id(64)

def test_lds_reverse_wave64():
    test_lds_reverse_across_waves(64)

TESTS = [test_multiwave_wave64, test_lds_reverse_wave64, test_multiwave_multigroup_global_id, test_lds_reverse_across_waves, test_store_lane_ids, test_readfirstlane_after_narrowed_write, test_waterfall_loop, test_image_store_phi_split]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t(); print("PASS", t.__name__)
        except Exception as e:
            bad += 1; print("FAIL", t.__name__, str(e)[:500])
    sys.exit(1 if bad else 0)
