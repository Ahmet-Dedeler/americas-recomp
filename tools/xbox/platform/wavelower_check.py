"""Check the lowered lane intrinsics against a software model, on a dump of a 64-thread-group kernel (wavelower kernel in docs/xbox/PLATFORM_SURFACE.md)
run with the wave size pinned to 64: per thread input v; output 12 dwords:
 [swizzle30, swizzle60, swizzle120, readlane(31)] [ballot lo, ballot hi, mbcnt64(ballot), bcnt1(ballot)] [cndmask(0xAAAAAAAA..;{0,1}), cndmask(ballot;{5,9}), makeuniform(v), laneid]
usage: python -I wavelower_check.py <u0.bin> <u1.bin> [groups]      The model uses the same formulas as the lowering (ds_swizzle bitmask mode, v_cndmask picks src1 when the mask bit is set), so this checks the plumbing, not the formulas."""
import struct, sys
u0 = open(sys.argv[1], "rb").read(); u1 = open(sys.argv[2], "rb").read()
groups = int(sys.argv[3]) if len(sys.argv) > 3 else 64
bad = {}; first = {}
def chk(name, g, lane, got, exp):
    if got != exp:
        bad[name] = bad.get(name, 0) + 1; first.setdefault(name, (g, lane, got, exp))
def swz(v, L, off):
    t = (((L & (off & 31)) | ((off >> 5) & 31)) ^ ((off >> 10) & 31)) | (L & ~31)
    return v[t]
for g in range(groups):
    v = [struct.unpack_from("<I", u0, 16 * (g * 64 + L))[0] for L in range(64)]
    ball = 0
    for L in range(64): ball |= (v[L] & 1) << L
    lo, hi = ball & 0xFFFFFFFF, ball >> 32
    for L in range(64):
        o = struct.unpack_from("<12I", u1, 48 * (g * 64 + L))
        chk("swizzle30", g, L, o[0], swz(v, L, 30)); chk("swizzle60", g, L, o[1], swz(v, L, 60)); chk("swizzle120", g, L, o[2], swz(v, L, 120))
        chk("readlane31", g, L, o[3], v[31])
        chk("ballot_lo", g, L, o[4], lo); chk("ballot_hi", g, L, o[5], hi)
        chk("mbcnt64", g, L, o[6], bin(ball & ((1 << L) - 1)).count("1"))
        chk("bcnt1", g, L, o[7], bin(ball).count("1"))
        chk("cndmask_const", g, L, o[8], (0xAAAAAAAAAAAAAAAA >> L) & 1)
        chk("cndmask_ballot", g, L, o[9], 9 if (ball >> L) & 1 else 5)
        chk("makeuniform", g, L, o[10], v[0]); chk("laneid", g, L, o[11], L)
print("%d groups x 64 lanes checked; mismatches: %s" % (groups, bad or "none"))
for k, x in first.items(): print("first", k, "(group, lane, got, expected)", x)
