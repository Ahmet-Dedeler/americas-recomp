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

def test_image_load_after_store_same_texel():
    # read-modify-write: store (lane, 2, 3.5, 1) to a texel, load the same texel back in the same invocation and store the loaded value.
    # The load has to see this invocation's own store; a sampled-image fetch next to the storage-image write can return the old (-1) texel.
    p = rdna_asm.Program()
    p.smem('s_load_dwordx8', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop1('v_cvt_f32_u32', 4, 'v0')
    p.vop1('v_mov_b32', 5, ('lit', 0x40000000))
    p.vop1('v_mov_b32', 6, ('lit', 0x40600000))
    p.vop1('v_mov_b32', 7, ('lit', 0x3f800000))
    p.vop1('v_mov_b32', 1, 0)
    p.mimg('image_store', 4, 0, 8)
    p.mimg('image_load', 10, 0, 8)
    p.sopp('s_waitcnt', 0)
    p.mimg('image_store', 10, 0, 8)
    p.sopp('s_endpgm')
    r = run(p.bytes(), {0: (256, None)}, [], extra=["image 1 32 1 clear -1 -1 -1 -1", "timg 0 0 1"])
    got = _image_pixels(r['out'], 1)
    want = tuple(v for lane in range(32) for v in (float(lane), 2.0, 3.5, 1.0))
    assert got == want, got[:8]

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

def test_readfirstlane_wave64_upper_half():
    """Wave64: exec narrowed to lanes above 40, so the first active lane is in the upper 32. v_readfirstlane must return lane 41's value
    to every lane once exec is restored, also where the host runs the wave as 32-wide subgroups with two guest lanes per invocation."""
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.vop2('v_add_nc_u32', 2, 1000, 'v0')
    p.sop1('s_mov_b64', 12, rdna_asm.EXEC_LO)
    p.vopc('v_cmpx_lt_u32', 40, 'v0')
    p.vop1('v_readfirstlane_b32', 0, 'v2')
    p.sop1('s_mov_b64', rdna_asm.EXEC_LO, 's12')
    p.vop1('v_mov_b32', 3, 's0')
    p.mubuf('buffer_store_dword', 3, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    r = run(p.bytes(), {0: (256, None), 1: (256, None)}, vsharp(0, 0, 1, 256), wave=64)
    got = struct.unpack('<64I', r[1][:256])
    assert got == (1041,) * 64, [(i, got[i]) for i in range(64) if got[i] != 1041][:6]

def test_multiwave_wave64():
    test_multiwave_multigroup_global_id(64)

def test_lds_reverse_wave64():
    test_lds_reverse_across_waves(64)

FMT_16_16_FLOAT, FMT_8_8_8_8_UNORM, FMT_8_8_8_8_UINT, FMT_16_16_16_16_SNORM, FMT_32_FLOAT = 29, 56, 60, 66, 22
SEL_0, SEL_1, SEL_X, SEL_Y, SEL_Z, SEL_W = 0, 1, 4, 5, 6, 7
IDENTITY = SEL_X | SEL_Y << 3 | SEL_Z << 6 | SEL_W << 9

def fsharp(srt_region, off, target, stride, records, fmt, dst_sel=IDENTITY):
    """scenario lines writing a structured, formatted V# (OOB mode 0: index < records) for region `target`"""
    return [f"ptrlo {srt_region} {off} {target}", f"ptrhi {srt_region} {off + 4} {target} {stride << 16}",
            f"poke {srt_region} {off + 8} {records}", f"poke {srt_region} {off + 12} {dst_sel | fmt << 12}"]

def _format_load(data, fmt, stride, dst_sel=IDENTITY, tformat=None, components=4, records=32):
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.smem('s_load_dwordx4', 12, 4, 16)
    p.sopp('s_waitcnt', 0)
    suffix = 'xyzw'[:components]
    if tformat is None:
        p.mubuf('buffer_load_format_' + suffix, 2, 0, 8, offen=False, idxen=True)
    else:
        p.mtbuf('tbuffer_load_format_' + suffix, 2, 0, 8, tformat)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 4, 'v0')
    p.mubuf('buffer_store_dword' + ('x%d' % components if components > 1 else ''), 2, 1, 12)
    p.sopp('s_endpgm')
    lines = fsharp(0, 0, 1, stride, records, fmt, dst_sel) + vsharp(0, 16, 2, 512)
    r = run(p.bytes(), {0: (256, None), 1: (len(data), data), 2: (512, None)}, lines)
    return [struct.unpack_from('<%dI' % components, r[2], 16 * lane) for lane in range(32)]

def _f32(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]

def _f32_round(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]

def _close(got, want, ulps=1):
    return abs(got - want) <= ulps * max(abs(want), 1.0) * 2.0 ** -23

def test_load_format_unorm8():
    data = bytes((lane * 37 + c * 71 + 5) & 0xff for lane in range(32) for c in range(4))
    got = _format_load(data, FMT_8_8_8_8_UNORM, 4)
    for lane in range(32):
        want = [data[4 * lane + c] / 255.0 for c in range(4)]
        assert all(_close(_f32(g), w) for g, w in zip(got[lane], want)), ("lane", lane, [_f32(g) for g in got[lane]], want)

def test_load_format_snorm16_clamps_min():
    values = [-32768, -32767, 32767, 0, -1, 1, 16384, -16384]
    data = b"".join(struct.pack('<4h', *(values[(lane + c) % 8] for c in range(4))) for lane in range(32))
    got = _format_load(data, FMT_16_16_16_16_SNORM, 8)
    for lane in range(32):
        want = [max(values[(lane + c) % 8] / 32767.0, -1.0) for c in range(4)]
        assert all(_close(_f32(g), w) for g, w in zip(got[lane], want)), ("lane", lane, [_f32(g) for g in got[lane]], want)

def test_load_format_dst_sel():
    data = bytes((lane * 4 + c) & 0xff for lane in range(32) for c in range(4))
    got = _format_load(data, FMT_8_8_8_8_UINT, 4, SEL_W | SEL_Z << 3 | SEL_0 << 6 | SEL_1 << 9)
    want = [(data[4 * lane + 3], data[4 * lane + 2], 0, 1) for lane in range(32)]
    assert got == want, (got[:3], want[:3])

def test_load_format_missing_components_default():
    halves = [0x3c00, 0xc000, 0x3555, 0x7bff, 0x0001, 0x8000, 0x4248, 0xbe00]
    data = b"".join(struct.pack('<2H', halves[lane % 8], halves[(lane + 3) % 8]) for lane in range(32))
    got = _format_load(data, FMT_16_16_FLOAT, 4)
    for lane in range(32):
        x, y = struct.unpack_from('<2e', data, 4 * lane)
        want = (struct.unpack('<I', struct.pack('<f', x))[0], struct.unpack('<I', struct.pack('<f', y))[0], 0, 0x3f800000)
        assert got[lane] == want, ("lane", lane, [hex(g) for g in got[lane]], [hex(w) for w in want])

def test_tbuffer_load_instruction_format():
    data = bytes((lane * 53 + c * 29 + 1) & 0xff for lane in range(32) for c in range(4))
    got = _format_load(data, FMT_32_FLOAT, 4, tformat=FMT_8_8_8_8_UNORM)
    for lane in range(32):
        want = [data[4 * lane + c] / 255.0 for c in range(4)]
        assert all(_close(_f32(g), w) for g, w in zip(got[lane], want)), ("lane", lane, [_f32(g) for g in got[lane]], want)

def test_tbuffer_load_missing_components_default():
    data = b"".join(struct.pack('<2e', lane * 0.5, -lane * 0.25) for lane in range(32))
    got = _format_load(data, FMT_32_FLOAT, 4, tformat=FMT_16_16_FLOAT)
    for lane in range(32):
        want = (struct.unpack('<I', struct.pack('<f', lane * 0.5))[0], struct.unpack('<I', struct.pack('<f', -lane * 0.25))[0], 0, 0)
        assert got[lane] == want, ("lane", lane, [hex(g) for g in got[lane]], [hex(w) for w in want])

def test_store_format_unorm8():
    vals = [[(((lane * 7 + c * 61) % 300) - 20 + 0.25) / 255.0 for c in range(4)] for lane in range(32)]
    for row in vals:
        for v in row:
            assert abs((min(max(v, 0.0), 1.0) * 255.0) % 1.0 - 0.5) > 0.05, v
    data = b"".join(struct.pack('<4f', *row) for row in vals)
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.smem('s_load_dwordx4', 12, 4, 16)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 4, 'v0')
    p.mubuf('buffer_load_dwordx4', 2, 1, 8)
    p.sopp('s_waitcnt', 0)
    p.mubuf('buffer_store_format_xyzw', 2, 0, 12, offen=False, idxen=True)
    p.sopp('s_endpgm')
    lines = vsharp(0, 0, 1, len(data)) + fsharp(0, 16, 2, 4, 32, FMT_8_8_8_8_UNORM)
    r = run(p.bytes(), {0: (256, None), 1: (len(data), data), 2: (128, None)}, lines)
    want = bytes(int(min(max(_f32_round(v), 0.0), 1.0) * 255.0 + 0.5) for row in vals for v in row)
    assert r[2] == want, [(i, r[2][i], want[i]) for i in range(128) if r[2][i] != want[i]][:8]

def test_load_format_runtime_descriptor_missing_components():
    halves = [0x3c00, 0xc000, 0x3555, 0x7bff, 0x0001, 0x8000, 0x4248, 0xbe00]
    data = b"".join(struct.pack('<2H', halves[lane % 8], halves[(lane + 3) % 8]) for lane in range(32))
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 12, 4, 16)
    p.smem('s_load_dwordx4', 16, 4, 32)
    p.sopp('s_waitcnt', 0)
    p.vop1('v_mov_b32', 1, 0)
    p.mubuf('buffer_load_dwordx4', 6, 1, 16)
    p.sopp('s_waitcnt', 0)
    for k in range(4):
        p.vop1('v_readfirstlane_b32', 8 + k, 'v%d' % (6 + k))
    p.mubuf('buffer_load_format_xyzw', 2, 0, 8, offen=False, idxen=True)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 4, 'v0')
    p.mubuf('buffer_store_dwordx4', 2, 1, 12)
    p.sopp('s_endpgm')
    lines = fsharp(3, 0, 1, 4, 20, FMT_16_16_FLOAT) + vsharp(0, 16, 2, 512) + vsharp(0, 32, 3, 64)
    r = run(p.bytes(), {0: (256, None), 1: (len(data), data), 2: (512, None), 3: (64, None)}, lines)
    for lane in range(32):
        x, y = struct.unpack_from('<2e', data, 4 * lane)
        want = (struct.unpack('<I', struct.pack('<f', x))[0], struct.unpack('<I', struct.pack('<f', y))[0], 0, 0x3f800000) if lane < 20 else (0, 0, 0, 0)
        got = struct.unpack_from('<4I', r[2], 16 * lane)
        assert got == want, ("lane", lane, [hex(g) for g in got], [hex(w) for w in want])

def test_load_format_out_of_range_missing_components():
    data = b"".join(struct.pack('<2e', 1.0 + lane, -2.0) for lane in range(32))
    one = 0x3f800000
    for dst_sel, oob in ((IDENTITY, (0, 0, 0, 0)), (SEL_X | SEL_1 << 3 | SEL_0 << 6 | SEL_W << 9, (0, one, 0, 0))):
        got = _format_load(data, FMT_16_16_FLOAT, 4, dst_sel, records=20)
        for lane in range(32):
            x = struct.unpack('<I', struct.pack('<f', 1.0 + lane))[0]
            y = struct.unpack('<I', struct.pack('<f', -2.0))[0]
            inside = (x, y, 0, one) if dst_sel == IDENTITY else (x, one, 0, one)
            want = inside if lane < 20 else oob
            assert got[lane] == want, ("dst_sel", hex(dst_sel), "lane", lane, [hex(g) for g in got[lane]], [hex(w) for w in want])

def _run_image_table(selector):
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 16, 4, 128)
    p.sopp('s_waitcnt', 0)
    p.vop1('v_mov_b32', 1, 0)
    p.mubuf('buffer_load_dword', 2, 1, 16)
    p.sopp('s_waitcnt', 0)
    p.vop1('v_readfirstlane_b32', 0, 'v2')
    p.sop2('s_lshl_b32', 0, 's0', 5)
    p.smem('s_load_dwordx8', 8, 4, 0, soffset='s0')
    p.sopp('s_waitcnt', 0)
    p.vop1('v_cvt_f32_u32', 4, 'v0')
    p.vop1('v_mov_b32', 5, ('lit', 0x40000000))
    p.vop1('v_mov_b32', 6, ('lit', 0x40600000))
    p.vop1('v_mov_b32', 7, ('lit', 0x3f800000))
    p.vop1('v_mov_b32', 1, 0)
    p.mimg('image_store', 4, 0, 8)
    p.sopp('s_endpgm')
    images = ["image %d 32 1 clear -1 -1 -1 -1" % i for i in (1, 2, 3)]
    pokes = images + ["timg 0 0 1", "timg 0 32 2", "timg 0 64 3"] + vsharp(0, 128, 4, 16) + [f"poke 4 0 {selector}"]
    return run(p.bytes(), {0: (256, None), 4: (16, None)}, [], extra=pokes)

def test_image_table_runtime_index():
    want = tuple(v for lane in range(32) for v in (float(lane), 2.0, 3.5, 1.0))
    for selector in (0, 1, 2):
        r = _run_image_table(selector)
        for i in (1, 2, 3):
            got = _image_pixels(r['out'], i)
            if i == selector + 1:
                assert got == want, ("selector", selector, "image", i, got[:8])
            else:
                assert got == tuple([-1.0] * 128), ("selector", selector, "image", i, "was written", got[:8])

def test_image_table_runtime_index_load():
    for selector in (0, 2):
        p = rdna_asm.Program()
        p.smem('s_load_dwordx4', 16, 4, 128)
        p.smem('s_load_dwordx4', 20, 4, 144)
        p.sopp('s_waitcnt', 0)
        p.vop1('v_mov_b32', 1, 0)
        p.mubuf('buffer_load_dword', 2, 1, 16)
        p.sopp('s_waitcnt', 0)
        p.vop1('v_readfirstlane_b32', 0, 'v2')
        p.sop2('s_lshl_b32', 0, 's0', 5)
        p.smem('s_load_dwordx8', 8, 4, 0, soffset='s0')
        p.sopp('s_waitcnt', 0)
        p.vop1('v_mov_b32', 1, 0)
        p.mimg('image_load', 4, 0, 8)
        p.sopp('s_waitcnt', 0)
        p.vop2('v_lshlrev_b32', 1, 4, 'v0')
        p.mubuf('buffer_store_dwordx4', 4, 1, 20)
        p.sopp('s_endpgm')
        images = ["image %d 32 1 clear %d %d %d %d" % (i, 10 * i, 10 * i + 1, 10 * i + 2, 10 * i + 3) for i in (1, 2, 3)]
        pokes = images + ["timg 0 0 1", "timg 0 32 2", "timg 0 64 3"] + vsharp(0, 128, 4, 16) + vsharp(0, 144, 5, 512) + [f"poke 4 0 {selector}"]
        r = run(p.bytes(), {0: (256, None), 4: (16, None), 5: (512, None)}, [], extra=pokes)
        i = selector + 1
        got = struct.unpack('<128f', r[5])
        assert got == tuple([10.0 * i, 10.0 * i + 1, 10.0 * i + 2, 10.0 * i + 3] * 32), ("selector", selector, got[:8])

TESTS = [test_image_table_runtime_index, test_image_table_runtime_index_load, test_image_load_after_store_same_texel, test_readfirstlane_wave64_upper_half, test_multiwave_wave64, test_lds_reverse_wave64, test_multiwave_multigroup_global_id, test_lds_reverse_across_waves, test_store_lane_ids, test_readfirstlane_after_narrowed_write, test_waterfall_loop, test_image_store_phi_split,
         test_load_format_unorm8, test_load_format_snorm16_clamps_min, test_load_format_dst_sel, test_load_format_missing_components_default,
         test_tbuffer_load_instruction_format, test_tbuffer_load_missing_components_default, test_store_format_unorm8,
         test_load_format_runtime_descriptor_missing_components,
         test_load_format_out_of_range_missing_components]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t(); print("PASS", t.__name__)
        except Exception as e:
            bad += 1; print("FAIL", t.__name__, str(e)[:500])
    sys.exit(1 if bad else 0)
