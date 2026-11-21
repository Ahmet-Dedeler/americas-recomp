"""Check the lowered Med3_F32 / V_PK_MIN_F16 / V_PK_MAX_F16 against a software model, on dumps made by dispatch_test (DT_RAW=1 DT_DUMP=<prefix>)
of the kernel in docs: input per case = 4 dwords (a, b, c, unused; the halves are a.lo, a.hi, b.lo, b.hi); output = med3(a,b,c) as f32, then min pair, max pair (halves).
usage: python -I oplower_check.py <prefix>_gpu_u0.bin <prefix>_gpu_u1.bin [cases]
RDNA semantics modelled: min/max return the other operand when one is NaN; NaN results compare equal whatever the payload; sign of zero and f32/f16
denormals are not distinguished (a GPU may flush them)."""
import struct, sys, math

def f32(u): return struct.unpack("<f", struct.pack("<I", u))[0]
def f16(u): return struct.unpack("<e", struct.pack("<H", u))[0]
def norm(x, small):
    if x != x: return "nan"
    if abs(x) < small: return 0.0
    return x
def vmin(a, b):
    if a != a: return b
    if b != b: return a
    return min(a, b)
def vmax(a, b):
    if a != a: return b
    if b != b: return a
    return max(a, b)
def med3(a, b, c): return vmax(vmin(a, b), vmin(vmax(a, b), c))

u0 = open(sys.argv[1], "rb").read(); u1 = open(sys.argv[2], "rb").read()
n = int(sys.argv[3]) if len(sys.argv) > 3 else 262144
bad = {"med3": 0, "pkmin": 0, "pkmax": 0}; nan_in = 0; first = {}
for i in range(n):
    a, b, c, _ = struct.unpack_from("<4I", u0, 16 * i)
    h = struct.unpack_from("<4H", u0, 16 * i)
    r0, = struct.unpack_from("<I", u1, 16 * i); o = struct.unpack_from("<4H", u1, 16 * i + 4)   # o: min.lo min.hi max.lo max.hi
    fa, fb, fc = f32(a), f32(b), f32(c)
    ex = med3(fa, fb, fc)
    if norm(f32(r0), 1.2e-38) != norm(ex, 1.2e-38):
        bad["med3"] += 1; first.setdefault("med3", (i, hex(a), hex(b), hex(c), hex(r0), ex))
    x = [f16(h[0]), f16(h[1])]; y = [f16(h[2]), f16(h[3])]
    for k, (name, fn, off) in enumerate((("pkmin", vmin, 0), ("pkmax", vmax, 2))):
        for lane in range(2):
            e = fn(x[lane], y[lane]); g = f16(o[off + lane])
            if norm(g, 6.2e-5) != norm(e, 6.2e-5):
                bad[name] += 1; first.setdefault(name, (i, lane, hex(h[lane]), hex(h[2 + lane]), hex(o[off + lane]), e))
print("%d cases checked; mismatches: %s" % (n, bad))
for k, v in first.items(): print("first", k, v)
