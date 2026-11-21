"""Rewrite Xbox DXIL text (the output of `dxc -dumpbin`) so it no longer calls the dxcxbox `@xbox.*` intrinsics.

Each intrinsic that has a standard DXIL equivalent is replaced by it; a shader that still calls an intrinsic with no replacement is
reported (exit status 1). The first argument of every xbox.* call (an instruction number) is dropped. The Xbox target triple
(dxil-ms-xb2) and the `xbox.compiler.options` module metadata are rewritten, because the standard validator rejects them.
The Xbox validator also accepts first-class aggregates such as `{ half, half }`; standard DXIL does not, so every such pair value is
split into two scalars (its insertvalue/extractvalue chains disappear).

usage: python -I xbox_dxil_lower.py in.ll out.ll        then: dxasm out.ll out.dxil

Lowered so far (more are added as shaders need them):
  Med3_F32                                                     ->  max(min(a,b), min(max(a,b),c)) with dx.op.binary.f32
  V_PK_MIN_F16 / V_PK_MAX_F16 ({half,half} lane-wise min/max)  ->  dx.op.binary.f16 (FMin 36 / FMax 35) on each half
"""
import re, sys

OPCODE = {"MIN": 36, "MAX": 35}
PAIR = r"\{ half, half \}"


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


def lower(text):
    n_lowered = 0
    pairs = {}            # SSA name of a { half, half } value -> (scalar0, scalar1)
    alias = {}            # SSA name of a scalar produced by extractvalue -> the scalar that replaces it
    out = []
    counter = [0]

    def fresh():
        counter[0] += 1
        return "%%xl.%d" % counter[0]

    def sub(line):
        if not alias:
            return line
        return re.sub(r"%[\w.]+", lambda m: alias.get(m.group(0), m.group(0)), line)

    ins_re = re.compile(r"^\s*(%[\w.]+) = insertvalue " + PAIR + r" (undef|%[\w.]+), half (%[\w.]+|[-+\w.]+), ([01])\s*$")
    ext_re = re.compile(r"^\s*(%[\w.]+) = extractvalue " + PAIR + r" (%[\w.]+), ([01])\s*$")
    med3_re = re.compile(r"^(\s*)(%[\w.]+) = call float @xbox\.Med3_F32\(i32 \d+, float ([^,]+), float ([^,]+), float ([^,)]+)\)")
    pk_re = re.compile(r"^(\s*)(%[\w.]+) = call " + PAIR + r" @xbox\.V_PK_(MIN|MAX)_F16\(i32 \d+, " + PAIR + r" (%[\w.]+), " + PAIR + r" (%[\w.]+)\)")
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
        m = med3_re.match(line)
        if m:
            # v_med3_f32(a, b, c) = max(min(a, b), min(max(a, b), c))
            ind, dst, a, b, c = m.groups()
            lo, hi, t = fresh(), fresh(), fresh()
            out.append("%s%s = call float @dx.op.binary.f32(i32 36, float %s, float %s)" % (ind, lo, a, b))
            out.append("%s%s = call float @dx.op.binary.f32(i32 35, float %s, float %s)" % (ind, hi, a, b))
            out.append("%s%s = call float @dx.op.binary.f32(i32 36, float %s, float %s)" % (ind, t, hi, c))
            out.append("%s%s = call float @dx.op.binary.f32(i32 35, float %s, float %s)" % (ind, dst, lo, t))
            n_lowered += 1
            continue
        m = pk_re.match(line)
        if m:
            ind, dst, kind, a, b = m.groups()
            res = []
            for i in range(2):
                r = fresh()
                out.append("%s%s = call half @dx.op.binary.f16(i32 %d, half %s, half %s)" % (ind, r, OPCODE[kind], pairs[a][i], pairs[b][i]))
                res.append(r)
            pairs[dst] = tuple(res)
            n_lowered += 1
            continue
        out.append(line)
    res = "\n".join(out)
    res = re.sub(r'target triple = "dxil-ms-xb\d*"', 'target triple = "dxil-ms-dx"', res)
    res = re.sub(r"!xbox\.compiler\.options = [^\n]*\n", "", res)
    if n_lowered:
        if "@dx.op.binary.f32(i32 3" in res and "declare float @dx.op.binary.f32" not in res:
            res = res.replace("\ndeclare ", "\n; Function Attrs: nounwind readnone\ndeclare float @dx.op.binary.f32(i32, float, float) #0\n\ndeclare ", 1)
        for name in ("V_PK_MIN_F16", "V_PK_MAX_F16", "Med3_F32"):
            res = re.sub(r"(; Function Attrs:[^\n]*\n)?declare [^\n]*@xbox\." + name + r"\([^\n]*\n\n?", "", res)
        if "call half @dx.op.binary.f16" in res and "declare half @dx.op.binary.f16" not in res:
            res = res.replace("\ndeclare ", "\n; Function Attrs: nounwind readnone\ndeclare half @dx.op.binary.f16(i32, half, half) #0\n\ndeclare ", 1)
    res = renumber(res)
    left = set(re.findall(r"call [^@\n]*@(xbox\.[\w.]+)\(", res))
    return res, n_lowered, left


if __name__ == "__main__":
    text = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
    res, n, left = lower(text)
    open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(res)
    print("lowered %d call(s); intrinsics still called: %s" % (n, sorted(left) or "none"))
    sys.exit(1 if left else 0)
