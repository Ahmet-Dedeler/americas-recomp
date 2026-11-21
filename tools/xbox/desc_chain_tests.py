"""Descriptor chains: descriptors selected through an index or pointer that the shader loads from memory at run time."""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rdna_asm
import exec_tests as T

HEAP, FIRST = 2, 10

def _heap_lines(count, size):
    lines = []
    for i in range(count):
        lines += T.vsharp(HEAP, i * 16, FIRST + i, size)
    return lines

def _check_one_written(r, chosen, count, size):
    want = tuple(1000 + i for i in range(32))
    for i in range(count):
        got = struct.unpack('<32I', r[FIRST + i][:128])
        if i == chosen:
            assert got == want, ("index", chosen, "region", FIRST + i, got[:8])
        else:
            assert got == (0,) * 32, ("index", chosen, "region", FIRST + i, "was written", got[:8])

def _store_lane_ids(p, vsharp_sgpr):
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.vop2('v_add_nc_u32', 2, 1000, 'v0')
    p.mubuf('buffer_store_dword', 2, 1, vsharp_sgpr, 128, 0)
    p.sopp('s_endpgm')

def test_scalar_index_from_buffer():
    """SRT dword 0 = index, dwords 2-3 = heap pointer. V# = heap[index]; the index exists only in memory."""
    count, size = 4, 128
    for chosen in range(count):
        p = rdna_asm.Program()
        p.smem('s_load_dwordx4', 8, 4, 0)
        p.sopp('s_waitcnt', 0)
        p.sop2('s_lshl_b32', 8, 's8', 4)
        p.smem('s_load_dwordx4', 12, 10, 0, soffset='s8')
        p.sopp('s_waitcnt', 0)
        _store_lane_ids(p, 12)
        regions = {0: (256, None), HEAP: (count * 16, None)}
        regions.update({FIRST + i: (size, None) for i in range(count)})
        pokes = [f"poke 0 0 {chosen}", f"ptrlo 0 8 {HEAP}", f"ptrhi 0 12 {HEAP} 0"] + _heap_lines(count, size)
        r = T.run(p.bytes(), regions, pokes)
        _check_one_written(r, chosen, count, size)

def test_waterfall_over_buffer_loaded_index():
    """SRT: V# of the index buffer at 0, heap pointer at 16. Each lane loads its own heap index, and a waterfall loop picks heap[index] for
    the lanes that share it. Lane i uses index perm[i]; region FIRST + k must hold 1000 + i exactly for the lanes with perm[i] == k."""
    count, size, idx = 4, 128, 1
    perm = [(i * 7 + 3) % count for i in range(32)]
    p = rdna_asm.Program()
    p.smem('s_load_dwordx8', 8, 4, 0)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.mubuf('buffer_load_dword', 3, 1, 8, 128, 0)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_add_nc_u32', 2, 1000, 'v0')
    top = p.label()
    p.vop1('v_readfirstlane_b32', 0, 'v3')
    p.vopc('v_cmp_eq_u32', 's0', 'v3')
    p.sop1('s_and_saveexec_b32', 1, rdna_asm.VCC_LO)
    p.sop2('s_lshl_b32', 0, 's0', 4)
    p.smem('s_load_dwordx4', 16, 12, 0, soffset='s0')
    p.sopp('s_waitcnt', 0)
    p.mubuf('buffer_store_dword', 2, 1, 16, 128, 0)
    p.sop2('s_xor_b32', rdna_asm.EXEC_LO, rdna_asm.EXEC_LO, 's1')
    p.branch_to('s_cbranch_execnz', top)
    p.sopp('s_endpgm')
    regions = {0: (256, None), idx: (128, struct.pack('<32I', *perm)), HEAP: (count * 16, None)}
    regions.update({FIRST + i: (size, None) for i in range(count)})
    pokes = T.vsharp(0, 0, idx, 128) + [f"ptrlo 0 16 {HEAP}", f"ptrhi 0 20 {HEAP} 0"] + _heap_lines(count, size)
    r = T.run(p.bytes(), regions, pokes)
    for k in range(count):
        got = struct.unpack('<32I', r[FIRST + k][:128])
        want = tuple(1000 + i if perm[i] == k else 0 for i in range(32))
        assert got == want, ("region", FIRST + k, [(i, got[i], want[i]) for i in range(32) if got[i] != want[i]][:6])

def test_loop_carried_index_from_buffer():
    """SRT: indices[0..3] at 0, heap pointer at 16. for (i = 0; i != 4; ++i) heap[indices[i]] gets 1000 + lane + 100 * i at lane * 4.
    The descriptor changes every iteration, so no single value of the SRT selects it."""
    count, size = 4, 128
    order = [2, 0, 3, 1]
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 12, 4, 16)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 2, 'v0')
    p.vop2('v_add_nc_u32', 2, 1000, 'v0')
    p.sop1('s_mov_b32', 20, 0)
    top = p.label()
    p.sop2('s_lshl_b32', 21, 's20', 2)
    p.smem('s_load_dword', 22, 4, 0, soffset='s21')
    p.sopp('s_waitcnt', 0)
    p.sop2('s_lshl_b32', 22, 's22', 4)
    p.smem('s_load_dwordx4', 16, 12, 0, soffset='s22')
    p.sopp('s_waitcnt', 0)
    p.mubuf('buffer_store_dword', 2, 1, 16, 128, 0)
    p.vop2('v_add_nc_u32', 2, 100, 'v2')
    p.sop2('s_add_u32', 20, 's20', 1)
    p.sopc('s_cmp_eq_u32', 's20', 4)
    p.branch_to('s_cbranch_scc0', top)
    p.sopp('s_endpgm')
    regions = {0: (256, None), HEAP: (count * 16, None)}
    regions.update({FIRST + i: (size, None) for i in range(count)})
    pokes = [f"poke 0 {4 * i} {order[i]}" for i in range(count)] + [f"ptrlo 0 16 {HEAP}", f"ptrhi 0 20 {HEAP} 0"] + _heap_lines(count, size)
    r = T.run(p.bytes(), regions, pokes)
    for i, k in enumerate(order):
        got = struct.unpack('<32I', r[FIRST + k][:128])
        want = tuple(1000 + lane + 100 * i for lane in range(32))
        assert got == want, ("iteration", i, "region", FIRST + k, got[:6], want[:6])

TESTS = [test_loop_carried_index_from_buffer, test_scalar_index_from_buffer, test_waterfall_over_buffer_loaded_index]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t(); print("PASS", t.__name__)
        except Exception as e:
            bad += 1; print("FAIL", t.__name__, str(e)[:1500])
    sys.exit(1 if bad else 0)
