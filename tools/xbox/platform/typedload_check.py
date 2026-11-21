"""Check the lowered TypedLoad.i32 (format 65) on dumps made by dispatch_test (DT_RAW=1 DT_DUMP=<prefix>) of typedload_kernel.ll: thread t stores
the loads at byte offsets t and 5t+3 of u0 as two dwords at 8t of u1; each must equal that byte of u0, zero-extended.
usage: python -I typedload_check.py <prefix>_gpu_u0.bin <prefix>_gpu_u1.bin [threads]"""
import struct, sys

u0 = open(sys.argv[1], "rb").read(); u1 = open(sys.argv[2], "rb").read()
n = int(sys.argv[3]) if len(sys.argv) > 3 else 4096
bad, first = 0, None
for t in range(n):
    got = struct.unpack_from("<2I", u1, 8 * t)
    exp = (u0[t], u0[5 * t + 3])
    if got != exp:
        bad += 1
        first = first or (t, got, exp)
print("%d threads checked, %d mismatches%s" % (n, bad, "" if first is None else "; first %r" % (first,)))
sys.exit(1 if bad else 0)
