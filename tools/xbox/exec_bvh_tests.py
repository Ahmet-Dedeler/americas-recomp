"""image_bvh_intersect_ray execution tests: build small BVH node memory, run the recompiled instruction on the GPU, compare with a Python reference
(ray/slab test for box nodes, Moller-Trumbore for triangle nodes)."""
import math, os, struct, sys
sys.path.insert(0, os.path.dirname(__file__))
import rdna_asm
import exec_tests as T

INVALID = 0xffffffff
INF = float('inf')

def node_id(offset, kind):
    return (offset // 64) * 8 + kind

def f2u(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]

def box32_node(children, boxes):
    """children: 4 node ids, boxes: 4 x (min xyz, max xyz) -> 128 bytes (RDNA2 box32 layout)"""
    d = list(children)
    for lo, hi in boxes:
        d += [f2u(v) for v in lo] + [f2u(v) for v in hi]
    d += [0] * (32 - len(d))
    return struct.pack('<32I', *d)

def triangle_node(v0, v1, v2, tri_id):
    d = [f2u(c) for v in (v0, v1, v2) for c in v] + [0] * 6 + [tri_id]
    return struct.pack('<16I', *d)

def inv_dir(direction):
    return tuple((1.0 / c) if c != 0 else INF for c in direction)

def slab(origin, inv, extent, lo, hi, grow=0):
    enter = None
    leave = None
    for ax in range(3):
        t0 = (lo[ax] - origin[ax]) * inv[ax]
        t1 = (hi[ax] - origin[ax]) * inv[ax]
        near, far = (t0, t1) if inv[ax] >= 0 else (t1, t0)
        enter = near if enter is None else max(enter, near)
        leave = far if leave is None else min(leave, far)
    min_t = max(enter, 0.0)
    max_t = min(leave, extent)
    return (min_t <= max_t * (1.0 + grow * 2.0 ** -23)), min_t

def ref_box(children, boxes, origin, direction, extent, sort):
    inv = inv_dir(direction)
    hits = [slab(origin, inv, extent, lo, hi) for lo, hi in boxes]
    ids = [c if h else INVALID for c, (h, _) in zip(children, hits)]
    if not sort:
        return ids
    order = sorted(range(4), key=lambda i: (ids[i] == INVALID, hits[i][1]))
    return [ids[i] for i in order]

def ref_triangle(v0, v1, v2, origin, direction):
    sub = lambda a, b: tuple(x - y for x, y in zip(a, b))
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    cross = lambda a, b: (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    e1, e2, e3 = sub(v1, v0), sub(v2, v0), sub(origin, v0)
    s1, s2 = cross(direction, e2), cross(e3, e1)
    den = dot(s1, e1)
    t, u, v = dot(e2, s2) / den, dot(e3, s1) / den, dot(direction, s2) / den
    return t if (0 <= u <= 1 and 0 <= v <= 1 and u + v <= 1) else None

def half_bits(x):
    return struct.unpack('<H', struct.pack('<e', x))[0]

def box16_node(children, boxes):
    """bounds are fp16, two per dword (low half first): 16 dwords"""
    d = list(children)
    halves = [half_bits(v) for lo, hi in boxes for v in tuple(lo) + tuple(hi)]
    d += [halves[i] | (halves[i + 1] << 16) for i in range(0, len(halves), 2)]
    return struct.pack('<16I', *d)

def run_query(bvh_bytes, node, origin, direction, extent, sort=False, grow=0, node64=False, a16=False):
    """one ray; returns the four result dwords of lane 0"""
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)       # output V#
    p.smem('s_load_dwordx4', 12, 4, 16)     # BVH descriptor
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 4, 'v0')
    inv = inv_dir(direction)
    vals = [node] + ([0] if node64 else []) + [f2u(extent)] + [f2u(c) for c in origin]
    if a16:
        h = [half_bits(c) for c in tuple(direction) + tuple(inv)]
        vals += [h[0] | (h[1] << 16), h[2] | (h[3] << 16), h[4] | (h[5] << 16)]
    else:
        vals += [f2u(c) for c in direction] + [f2u(c) for c in inv]
    for i, v in enumerate(vals):
        p.vop1('v_mov_b32', 8 + i, ('lit', v))
    p.mimg('image_bvh64_intersect_ray' if node64 else 'image_bvh_intersect_ray', 4, 8, 12, dmask=0xf, dim=0, a16=a16)
    p.sopp('s_waitcnt', 0)
    p.mubuf('buffer_store_dwordx4', 4, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    pokes = T.vsharp(0, 0, 1, 1024) + [
        "ptr8lo 0 16 2", f"ptr8hi 0 20 2 {(grow << 23) | ((1 << 31) if sort else 0)}",
        "poke 0 24 0xffffffff", f"poke 0 28 {0x3ff | (0xb << 28)}"]
    r = T.run(p.bytes(), {0: (256, None), 1: (1024, None), 2: (len(bvh_bytes), bvh_bytes)}, pokes)
    return struct.unpack('<4I', r[1][:16])

CHILD_OFFSETS = (128, 192, 256, 320)
CHILDREN = [node_id(o, 0) for o in CHILD_OFFSETS]

def boxes_along_x(order, miss=None):
    out = []
    for slot, x in enumerate(order):
        y = 5.0 if slot == miss else 0.0
        out.append(((x, y, 0.0), (x + 1.0, y + 1.0, 1.0)))
    return out

def test_box32_unsorted():
    boxes = boxes_along_x([0.0, 2.0, 4.0, 6.0], miss=2)
    node = box32_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(node, node_id(0, 5), origin, direction, 100.0)
    want = ref_box(CHILDREN, boxes, origin, direction, 100.0, False)
    assert list(got) == want, (list(map(hex, got)), list(map(hex, want)))

def test_box32_sorted():
    boxes = boxes_along_x([6.0, 2.0, 4.0, 0.0], miss=1)       # slot order is not distance order, one miss
    node = box32_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(node, node_id(0, 5), origin, direction, 100.0, sort=True)
    want = ref_box(CHILDREN, boxes, origin, direction, 100.0, True)
    assert list(got) == want, (list(map(hex, got)), list(map(hex, want)))

def test_box32_extent_clips():
    boxes = boxes_along_x([0.0, 2.0, 4.0, 6.0])
    node = box32_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(node, node_id(0, 5), origin, direction, 3.5)    # boxes at x >= 4 start beyond the extent (t = 5, 7)
    want = ref_box(CHILDREN, boxes, origin, direction, 3.5, False)
    assert list(got) == want, (list(map(hex, got)), list(map(hex, want)))

def test_triangle_hit():
    v0, v1, v2 = (5.0, 0.0, 0.0), (5.0, 2.0, 0.0), (5.0, 0.0, 2.0)
    bvh = box32_node(CHILDREN, boxes_along_x([0, 2, 4, 6])) + b'\0' * 0 + triangle_node(v0, v1, v2, 0x1234)
    pad = 128 - len(bvh) % 128 if False else 0
    origin, direction = (0.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(bvh, node_id(128, 0), origin, direction, 100.0)
    t = got[0:1] and struct.unpack('<ff', struct.pack('<II', got[0], got[1]))
    want = ref_triangle(v0, v1, v2, origin, direction)
    assert want is not None and abs(t[0] / t[1] - want) < 1e-5 and got[2] == 0x1234, (got, t, want)

def test_triangle_miss():
    v0, v1, v2 = (5.0, 0.0, 0.0), (5.0, 2.0, 0.0), (5.0, 0.0, 2.0)
    bvh = box32_node(CHILDREN, boxes_along_x([0, 2, 4, 6])) + triangle_node(v0, v1, v2, 0x1234)
    origin, direction = (0.0, 1.5, 1.5), (1.0, 0.0, 0.0)             # passes outside the triangle
    got = run_query(bvh, node_id(128, 0), origin, direction, 100.0)
    t_num = struct.unpack('<f', struct.pack('<I', got[0]))[0]
    assert ref_triangle(v0, v1, v2, origin, direction) is None and t_num == INF, (got, t_num)

def _box16_case(sort, **kw):
    boxes = boxes_along_x([6.0, 2.0, 4.0, 0.0], miss=1)
    node = box16_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(node, node_id(0, 4), origin, direction, 100.0, sort=sort, **kw)
    want = ref_box(CHILDREN, boxes, origin, direction, 100.0, sort)
    assert list(got) == want, (list(map(hex, got)), list(map(hex, want)))

def test_box16_unsorted(): _box16_case(False)
def test_box16_sorted(): _box16_case(True)
def test_box32_node64(): 
    boxes = boxes_along_x([6.0, 2.0, 4.0, 0.0], miss=2)
    node = box32_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(node, node_id(0, 5), origin, direction, 100.0, sort=True, node64=True)
    want = ref_box(CHILDREN, boxes, origin, direction, 100.0, True)
    assert list(got) == want, (list(map(hex, got)), list(map(hex, want)))
def test_box32_a16():
    boxes = boxes_along_x([6.0, 2.0, 4.0, 0.0], miss=3)
    node = box32_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(node, node_id(0, 5), origin, direction, 100.0, sort=True, a16=True)
    want = ref_box(CHILDREN, boxes, origin, direction, 100.0, True)
    assert list(got) == want, (list(map(hex, got)), list(map(hex, want)))
def test_box16_node64_a16(): _box16_case(True, node64=True, a16=True)
def test_triangle_node64_a16():
    v0, v1, v2 = (5.0, 0.0, 0.0), (5.0, 2.0, 0.0), (5.0, 0.0, 2.0)
    bvh = box32_node(CHILDREN, boxes_along_x([0, 2, 4, 6])) + triangle_node(v0, v1, v2, 0x1234)
    origin, direction = (0.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    got = run_query(bvh, node_id(128, 0), origin, direction, 100.0, node64=True, a16=True)
    t = struct.unpack('<ff', struct.pack('<II', got[0], got[1]))
    assert abs(t[0] / t[1] - 5.0) < 1e-5 and got[2] == 0x1234, (got, t)

def test_box32_low_guest_addresses():
    # the node memory lives at a guest address below 4 GiB; the BDA page table maps it to the real device address
    boxes = boxes_along_x([6.0, 2.0, 4.0, 0.0], miss=1)
    node = box32_node(CHILDREN, boxes)
    origin, direction = (-1.0, 0.5, 0.5), (1.0, 0.0, 0.0)
    p = rdna_asm.Program()
    p.smem('s_load_dwordx4', 8, 4, 0)
    p.smem('s_load_dwordx4', 12, 4, 16)
    p.sopp('s_waitcnt', 0)
    p.vop2('v_lshlrev_b32', 1, 4, 'v0')
    inv = inv_dir(direction)
    vals = [node_id(0, 5), f2u(100.0)] + [f2u(c) for c in origin] + [f2u(c) for c in direction] + [f2u(c) for c in inv]
    for i, v in enumerate(vals):
        p.vop1('v_mov_b32', 8 + i, ('lit', v))
    p.mimg('image_bvh_intersect_ray', 4, 8, 12, dmask=0xf, dim=0)
    p.sopp('s_waitcnt', 0)
    p.mubuf('buffer_store_dwordx4', 4, 1, 8, 128, 0)
    p.sopp('s_endpgm')
    pokes = T.vsharp(0, 0, 1, 1024) + ["ptr8lo 0 16 2", f"ptr8hi 0 20 2 {1 << 31}", "poke 0 24 0xffffffff", f"poke 0 28 {0x3ff | (0xb << 28)}"]
    r = T.run(p.bytes(), {0: (256, None), 1: (1024, None), 2: (len(node), node)}, pokes, extra=["lowguest"])
    got = struct.unpack('<4I', r[1][:16])
    assert list(got) == ref_box(CHILDREN, boxes, origin, direction, 100.0, True), list(map(hex, got))

TESTS = [test_box32_low_guest_addresses, test_box16_unsorted, test_box16_sorted, test_box32_node64, test_box32_a16, test_box16_node64_a16, test_triangle_node64_a16, test_box32_unsorted, test_box32_sorted, test_box32_extent_clips, test_triangle_hit, test_triangle_miss]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t(); print("PASS", t.__name__)
        except Exception as e:
            bad += 1; print("FAIL", t.__name__, str(e)[:600])
    sys.exit(1 if bad else 0)
