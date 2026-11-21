"""Rewrite Xbox DXIL text (the output of `dxc -dumpbin`) so it no longer calls the dxcxbox `@xbox.*` intrinsics.

Each intrinsic that has a standard DXIL equivalent is replaced by it; a shader that still calls an intrinsic with no replacement is
reported (exit status 1). The first argument of every xbox.* call (an instruction number) is dropped. The Xbox target triple
(dxil-ms-xb2) and the `xbox.compiler.options` module metadata are rewritten, because the standard validator rejects them.
The Xbox validator also accepts first-class aggregates such as `{ half, half }` and `{ i32, i32 }` (a 64-bit lane mask); standard DXIL does not,
so every such pair value is split into two scalars (its insertvalue/extractvalue chains disappear).

usage: python -I xbox_dxil_lower.py in.ll out.ll        then: dxasm out.ll out.dxil

Lowered so far (more are added as shaders need them):
  Med3_F32                 max(min(a,b), min(max(a,b),c))                              dx.op.binary.f32
  V_PK_MIN_F16/MAX_F16     lane-wise min/max of a half pair                            dx.op.binary.f16
  GetLaneID                WaveGetLaneIndex
  MakeUniform              WaveReadLaneFirst (v_readfirstlane)
  ReadLane                 WaveReadLaneAt (v_readlane)
  LaneSwizzle              ds_swizzle_b32: offset bit 15 = 0 is the bitmask mode ((lane & and) | or) ^ xor inside a group of 32 lanes
                           (and = off[4:0], or = off[9:5], xor = off[14:10]); bit 15 = 1 is the quad-permute mode. -> WaveReadLaneAt
  Ballot64                 WaveActiveBallot, returning the low two dwords as the 64-bit mask
  V_CNDMASK_B32            (mask pair, {src0, src1}): the mask bit of this lane picks src1, else src0. Argument order inferred from the
                           Hillis-Steele prefix sums these shaders contain (masks 0xAAAAAAAA, 0xCCCCCCCC, 0xF0F0F0F0 with swizzles 30, 60, 120)
  MBCNT64                  number of set mask bits below this lane (v_mbcnt_lo + v_mbcnt_hi)
  S_BCNT1_U64              popcount of the 64-bit mask
  Min3/Max3_F32, Min3/Max3/Med3_U32, BFI, V_ALIGNBYTE_B32, WaveMin_U32, MakeUniform.i64   the obvious standard operations
The wave intrinsics assume the 64-lane waves the shaders were built for: run the result with the wave size pinned to 64 (dxil_sm66_wave.py).
"""
import re, sys

PAIR_RE = r"\{ (?:half, half|i32, i32) \}"


def renumber(text):
    """Unnamed values and blocks (%N, '; <label>:N') must be numbered consecutively; removing instructions leaves holes."""
    m = re.search(r"^define [^\n]*\{\n", text, re.M)
    if not m:
        return text
    start = m.end()
    end = text.index("\n}\n", start)
    body = text[start:end]
    mapping, n = {}, 1          # number 0 is the unlabeled entry block
    for line in body.split("\n"):
        lm = re.match(r"^; <label>:(\d+)", line)
        dm = re.match(r"^\s*%(\d+) = ", line)
        if lm:
            mapping[lm.group(1)] = str(n)
            n += 1
        elif dm:
            mapping[dm.group(1)] = str(n)
            n += 1
    body = re.sub(r"%(\d+)\b", lambda x: "%" + mapping.get(x.group(1), x.group(1)), body)
    body = re.sub(r"^; <label>:(\d+)", lambda x: "; <label>:" + mapping.get(x.group(1), x.group(1)), body, flags=re.M)
    return text[:start] + body + text[end:]


def split_args(s):
    """Split a call's argument text at top-level commas (braces nest)."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


# declarations a lowering may need: marker -> text
DECLS = {
    "@dx.op.binary.f32(": "declare float @dx.op.binary.f32(i32, float, float) #0",
    "@dx.op.binary.f16(": "declare half @dx.op.binary.f16(i32, half, half) #0",
    "@dx.op.waveGetLaneIndex(": "declare i32 @dx.op.waveGetLaneIndex(i32) #0",
    "@dx.op.waveReadLaneFirst.i32(": "declare i32 @dx.op.waveReadLaneFirst.i32(i32, i32) #1",
    "@dx.op.waveReadLaneAt.i32(": "declare i32 @dx.op.waveReadLaneAt.i32(i32, i32, i32) #1",
    "@dx.op.waveActiveBallot(": "declare %dx.types.fouri32 @dx.op.waveActiveBallot(i32, i1) #1",
    "@dx.op.unaryBits.i32(": "declare i32 @dx.op.unaryBits.i32(i32, i32) #0",
    "@dx.op.binary.i32(": "declare i32 @dx.op.binary.i32(i32, i32, i32) #0",
    "@dx.op.waveActiveOp.i32(": "declare i32 @dx.op.waveActiveOp.i32(i32, i32, i8, i8) #1",
    "@dx.op.waveReadLaneFirst.i64(": "declare i64 @dx.op.waveReadLaneFirst.i64(i32, i64) #1",
}


def insert_decls(text, decls):
    block = "\n".join(decls) + "\n\n"
    for prefix in ("declare ", "define "):
        m = re.search(r"^" + prefix, text, re.M)
        if m:
            return text[:m.start()] + block + text[m.start():]
    return text.rstrip("\n") + "\n\n" + block


def lower(text):
    n_lowered = 0
    pairs = {}            # SSA name of a pair value -> (scalar0, scalar1)
    alias = {}            # SSA name of a scalar produced by extractvalue -> the scalar that replaces it
    out = []
    counter = [0]
    used = set()

    def fresh():
        counter[0] += 1
        return "%%xl.%d" % counter[0]

    def sub(line):
        if not alias:
            return line
        return re.sub(r"%[\w.]+", lambda m: alias.get(m.group(0), m.group(0)), line)

    ins_re = re.compile(r"^\s*(%[\w.]+) = insertvalue " + PAIR_RE + r" (undef|%[\w.]+), (?:half|i32) (%[\w.]+|[-+\w.]+), ([01])\s*$")
    ext_re = re.compile(r"^\s*(%[\w.]+) = extractvalue " + PAIR_RE + r" (%[\w.]+), ([01])\s*$")
    call_re = re.compile(r"^(\s*)(%[\w.]+) = call ([^@]+?) @xbox\.([\w.]+)\(i32 \d+(?:, (.*?))?\)\s*(?:,\s*![^;]*)?(?:;.*)?$")

    def operand(a):
        """('%x' | literal pair) -> (s0, s1);  'i32 %x' -> '%x'"""
        m = re.match(r"^" + PAIR_RE + r" (.*)$", a)
        if m:
            v = m.group(1).strip()
            if v.startswith("{"):
                lit = re.findall(r"(?:half|i32) ([^,}\s]+)", v)
                return (lit[0], lit[1])
            return pairs[v]
        return a.split(" ", 1)[1]

    for line in text.split("\n"):
        line = sub(line)
        m = ins_re.match(line)
        if m:
            dst, src, val, idx = m.groups()
            cur = list(pairs.get(src, ("undef", "undef")))
            cur[int(idx)] = val
            pairs[dst] = tuple(cur)
            continue
        m = ext_re.match(line)
        if m and m.group(2) in pairs:
            alias[m.group(1)] = pairs[m.group(2)][int(m.group(3))]
            continue
        m = call_re.match(line)
        if not m:
            out.append(line)
            continue
        ind, dst, rty, name, argtext = m.groups()
        args = split_args(argtext or "")
        emit = lambda s: out.append(ind + s)

        def lane():
            l = fresh()
            emit("%s = call i32 @dx.op.waveGetLaneIndex(i32 111)" % l)
            used.add("@dx.op.waveGetLaneIndex(")
            return l

        def bin_(kind, ty, a, b, op):
            r = fresh()
            emit("%s = call %s @dx.op.binary.%s(i32 %d, %s %s, %s %s)" % (r, ty, kind, op, ty, a, ty, b))
            used.add("@dx.op.binary.%s(" % kind)
            return r

        def popc(v):
            r = fresh()
            emit("%s = call i32 @dx.op.unaryBits.i32(i32 31, i32 %s)" % (r, v))
            used.add("@dx.op.unaryBits.i32(")
            return r

        def i(op, a, b):
            r = fresh()
            emit("%s = %s i32 %s, %s" % (r, op, a, b))
            return r

        if name == "Med3_F32":
            a, b, c = [operand(x) for x in args]
            lo, hi = bin_("f32", "float", a, b, 36), bin_("f32", "float", a, b, 35)
            t = bin_("f32", "float", hi, c, 36)
            emit("%s = call float @dx.op.binary.f32(i32 35, float %s, float %s)" % (dst, lo, t))
        elif name in ("V_PK_MIN_F16", "V_PK_MAX_F16"):
            a, b = operand(args[0]), operand(args[1])
            op = 36 if name.endswith("MIN_F16") else 35
            pairs[dst] = tuple(bin_("f16", "half", a[k], b[k], op) for k in range(2))
        elif name == "GetLaneID":
            emit("%s = call i32 @dx.op.waveGetLaneIndex(i32 111)" % dst)
            used.add("@dx.op.waveGetLaneIndex(")
        elif name == "MakeUniform.i32":
            emit("%s = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 %s)" % (dst, operand(args[0])))
            used.add("@dx.op.waveReadLaneFirst.i32(")
        elif name == "ReadLane.i32":
            emit("%s = call i32 @dx.op.waveReadLaneAt.i32(i32 117, i32 %s, i32 %s)" % (dst, operand(args[0]), operand(args[1])))
            used.add("@dx.op.waveReadLaneAt.i32(")
        elif name == "LaneSwizzle.i32":
            v, off = operand(args[0]), int(operand(args[1]))
            l = lane()
            if off & 0x8000:                         # quad permute: lane' = (lane & ~3) | ((off >> 2*(lane&3)) & 3)
                q = i("and", l, 3)
                sh = i("shl", q, 1)
                s = i("lshr", str(off & 0xFF), sh)
                p = i("and", s, 3)
                base = i("and", l, -4)
                tgt = i("or", base, p)
            else:                                    # bitmask: ((lane & and) | or) ^ xor, inside a group of 32
                t1 = i("and", l, off & 31)
                t2 = i("or", t1, (off >> 5) & 31)
                t3 = i("xor", t2, (off >> 10) & 31)
                base = i("and", l, -32)
                tgt = i("or", base, t3)
            emit("%s = call i32 @dx.op.waveReadLaneAt.i32(i32 117, i32 %s, i32 %s)" % (dst, v, tgt))
            used.add("@dx.op.waveReadLaneAt.i32(")
        elif name == "Ballot64":
            c = fresh()
            emit("%s = icmp ne i32 %s, 0" % (c, operand(args[0])))
            b = fresh()
            emit("%s = call %%dx.types.fouri32 @dx.op.waveActiveBallot(i32 116, i1 %s)" % (b, c))
            used.add("@dx.op.waveActiveBallot(")
            lo, hi = fresh(), fresh()
            emit("%s = extractvalue %%dx.types.fouri32 %s, 0" % (lo, b))
            emit("%s = extractvalue %%dx.types.fouri32 %s, 1" % (hi, b))
            pairs[dst] = (lo, hi)
        elif name == "V_CNDMASK_B32":
            lo, hi = operand(args[0])
            s0, s1 = operand(args[1])
            l = lane()
            ishi = fresh()
            emit("%s = icmp uge i32 %s, 32" % (ishi, l))
            sh = i("and", l, 31)
            blo, bhi = i("lshr", lo, sh), i("lshr", hi, sh)
            w = fresh()
            emit("%s = select i1 %s, i32 %s, i32 %s" % (w, ishi, bhi, blo))
            bit = i("and", w, 1)
            c = fresh()
            emit("%s = icmp ne i32 %s, 0" % (c, bit))
            emit("%s = select i1 %s, i32 %s, i32 %s" % (dst, c, s1, s0))
        elif name == "MBCNT64":
            lo, hi = operand(args[0])
            l = lane()
            ishi = fresh()
            emit("%s = icmp uge i32 %s, 32" % (ishi, l))
            sh = i("and", l, 31)
            below = i("add", i("shl", "1", sh), -1)                 # (1 << sh) - 1
            lo_m = fresh()
            emit("%s = select i1 %s, i32 -1, i32 %s" % (lo_m, ishi, below))      # lanes >= 32 see all of the low word
            hi_m = fresh()
            emit("%s = select i1 %s, i32 %s, i32 0" % (hi_m, ishi, below))
            a = popc(i("and", lo, lo_m))
            b = popc(i("and", hi, hi_m))
            emit("%s = add i32 %s, %s" % (dst, a, b))
        elif name == "S_BCNT1_U64":
            lo, hi = operand(args[0])
            emit("%s = add i32 %s, %s" % (dst, popc(lo), popc(hi)))
        elif name in ("Min3_F32", "Max3_F32"):
            a, b, c = [operand(x) for x in args]
            op = 36 if name.startswith("Min") else 35
            emit("%s = call float @dx.op.binary.f32(i32 %d, float %s, float %s)" % (dst, op, bin_("f32", "float", a, b, op), c))
            used.add("@dx.op.binary.f32(")
        elif name in ("Min3_U32", "Max3_U32", "Med3_U32"):
            a, b, c = [operand(x) for x in args]
            used.add("@dx.op.binary.i32(")
            def ub(op, x, y, d=None):
                r = d or fresh()
                emit("%s = call i32 @dx.op.binary.i32(i32 %d, i32 %s, i32 %s)" % (r, op, x, y))
                return r
            if name == "Min3_U32":
                ub(40, ub(40, a, b), c, dst)
            elif name == "Max3_U32":
                ub(39, ub(39, a, b), c, dst)
            else:                                    # median: max(min(a,b), min(max(a,b), c))
                lo, hi = ub(40, a, b), ub(39, a, b)
                ub(39, lo, ub(40, hi, c), dst)
        elif name == "BFI":                          # v_bfi_b32: (mask & x) | (~mask & y)
            m, x, y = [operand(a) for a in args]
            nm = i("xor", m, -1)
            emit("%s = or i32 %s, %s" % (dst, i("and", m, x), i("and", nm, y)))
        elif name == "V_ALIGNBYTE_B32":              # ({hi, lo} >> (8 * sh[1:0]))[31:0]
            hi, lo, sh = [operand(a) for a in args]
            sb = i("shl", i("and", sh, 3), 3)
            right = i("lshr", lo, sb)
            nz = fresh()
            emit("%s = icmp ne i32 %s, 0" % (nz, sb))
            left = i("shl", hi, i("and", i("sub", 32, sb), 31))
            emit("%s = select i1 %s, i32 %s, i32 0" % (fresh(), nz, left))
            sel = "%%xl.%d" % counter[0]
            emit("%s = or i32 %s, %s" % (dst, right, sel))
        elif name == "WaveMin_U32":
            emit("%s = call i32 @dx.op.waveActiveOp.i32(i32 119, i32 %s, i8 2, i8 1)" % (dst, operand(args[0])))
            used.add("@dx.op.waveActiveOp.i32(")
        elif name == "MakeUniform.i64":
            emit("%s = call i64 @dx.op.waveReadLaneFirst.i64(i32 118, i64 %s)" % (dst, operand(args[0])))
            used.add("@dx.op.waveReadLaneFirst.i64(")
        else:
            out.append(line)
            continue
        n_lowered += 1
    res = "\n".join(out)
    res = re.sub(r'target triple = "dxil-ms-xb\d*"', 'target triple = "dxil-ms-dx"', res)
    res = re.sub(r"!xbox\.compiler\.options = [^\n]*\n", "", res)
    # drop declarations of intrinsics that are gone; add the standard ones that are now used
    still = set(re.findall(r"call [^@\n]*@(xbox\.[\w.]+)\(", res))
    for nm in set(re.findall(r"^declare [^@\n]*@(xbox\.[\w.]+)\(", res, re.M)):
        if nm not in still:
            res = re.sub(r"(; Function Attrs:[^\n]*\n)?declare [^\n]*@" + re.escape(nm) + r"\([^\n]*\n\n?", "", res)
    add = [DECLS[k] for k in sorted(used) if not re.search(r"^declare [^\n]*" + re.escape(k), res, re.M)]
    if add:
        res = insert_decls(res, add)
    if "%dx.types.fouri32" in res and "%dx.types.fouri32 = type" not in res:
        res = res.replace("%dx.types.Handle = type { i8* }\n", "%dx.types.Handle = type { i8* }\n%dx.types.fouri32 = type { i32, i32, i32, i32 }\n", 1)
    res = renumber(res)
    left = set(re.findall(r"call [^@\n]*@(xbox\.[\w.]+)\(", res))
    return res, n_lowered, left


if __name__ == "__main__":
    text = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
    res, n, left = lower(text)
    open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(res)
    print("lowered %d call(s); intrinsics still called: %s" % (n, sorted(left) or "none"))
    sys.exit(1 if left else 0)
