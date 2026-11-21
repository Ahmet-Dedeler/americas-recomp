"""Run recompiled real PIXEL shaders on the GPU and compare their colour exports with the CPU interpreter (cpu_ref.py).

Setup (shared by both sides, nothing here comes from the game):
  * exec.exe `stage ps`: one triangle covering an 8x8 target, 8 RGBA32F attachments cleared to -777.0, no blending
  * the vertex shader writes CONSTANT interpolants ATTR(n, c) at every vertex, so interpolation is the identity and every pixel
    sees the same inputs; the barycentric VGPRs (v0, v1) and every other input VGPR are modelled as unknown
  * export mode FP16_ABGR (SPI_SHADER_COL_FORMAT 4), as in tools/xbox/driver.cpp

Consequences, stated so a MATCH is not over-read:
  * it validates arithmetic, buffer/image loads and exports for ONE input vector, not interpolation, derivatives or position
  * a shader whose result depends on the barycentrics, on any undefined input VGPR, or on the lane (pixel) is SKIPped: the CPU is
    run with two different junk fillings of the unknown VGPRs and must agree with itself and across all 64 lanes
usage: cpu_compare_ps.py <out.json> [--ids a,b] [--max-bytes N] [--wave 32|64] [--workers N]
"""
import concurrent.futures as cf, json, os, struct, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_real as R
import cpu_ref as C
import cpu_compare as CC

GLSLANG = r"C:/VulkanSDK/1.4.363.1/Bin/glslangValidator.exe"
CLEAR = C.b32(-777.0)
TARGET = 8


def quad_pixel(lane):
    """lane -> (x, y) in the 8x8 target: quads of four consecutive lanes (x + 2 y inside the quad), quads in row-major order.
    Any bijection that keeps quads together is a valid CPU-side layout, because every pixel is computed independently of the
    wave layout except by the quad operations (derivatives, swizzles), which only look inside a quad."""
    q, i = lane // 4, lane % 4
    return (q % 4) * 2 + (i & 1), (q // 4) * 2 + (i >> 1)


PIXEL_LANE = {quad_pixel(l): l for l in range(C.WAVE)}

# SPI_PS_INPUT_ADDR bits: 1 = PERSP_CENTER (v0, v1), 8/9/10/11 = POS_X/Y/Z/W_FLOAT in the next VGPRs. Candidates tried in order.
INPUT_ADDRS = (0x2, 0x302, 0xf02)


def attr(n, c):
    """the constant interpolant value (exactly representable, 0.125 .. 1.625)"""
    return (1 + ((n * 4 + c) * 5) % 13) / 8.0


_VS = {}


def vertex_shader(count, workdir):
    if count in _VS:
        return _VS[count]
    glsl = ["#version 450"]
    for k in range(count):
        glsl.append("layout(location=%d) out vec4 o%d;" % (k, k))
    glsl.append("void main(){ vec2 p = vec2((gl_VertexIndex << 1) & 2, gl_VertexIndex & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.5, 1.0);")
    for k in range(count):
        glsl.append("o%d = vec4(%s);" % (k, ",".join("%.6f" % attr(k, c) for c in range(4))))
    glsl.append("}")
    d = tempfile.mkdtemp(prefix="vs_")
    src = os.path.join(d, "v.vert")
    open(src, "w").write("\n".join(glsl))
    out = os.path.join(d, "v.spv")
    p = subprocess.run([GLSLANG, "-V", src, "-o", out], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("glslang: " + p.stdout[-300:])
    _VS[count] = out.replace("\\", "/")
    return _VS[count]


def f16_rtz(x):
    """float -> binary16 bits, rounding toward zero (v_cvt_pkrtz_f16_f32)"""
    if x != x:
        return 0x7e00
    sign = 0x8000 if (C.b32(x) >> 31) else 0
    a = abs(x)
    if a == float("inf"):
        return sign | 0x7c00
    if a >= 65520.0:
        return sign | 0x7bff
    h = struct.unpack('<H', struct.pack('<e', a))[0]
    if C.f16(h) > a:
        h -= 1
    return sign | h


class PSWave(C.Wave):
    def __init__(self, code, mem, sgpr_init, junk, inputaddr=2):
        super().__init__(code, mem, sgpr_init)
        # the barycentrics and every input VGPR the model does not define are unknown: fill with a seeded junk pattern
        for r in range(0, 16):
            for lane in range(C.WAVE):
                self.v[r][lane] = (junk * 0x9e3779b1 + r * 0x85ebca6b + lane * 0xc2b2ae35) & C.M32 | 0x3f000000
        reg = 2
        for bit, fn in ((8, lambda x, y: x + 0.5), (9, lambda x, y: y + 0.5), (10, lambda x, y: 0.5), (11, lambda x, y: 1.0)):
            if inputaddr & (1 << bit):
                for lane in range(C.WAVE):
                    self.v[reg][lane] = C.b32(fn(*quad_pixel(lane)))
                reg += 1
        self.tg = [[[CLEAR] * 4 for _ in range(C.WAVE)] for _ in range(TARGET)]
        self.wr = [[[False] * 4 for _ in range(C.WAVE)] for _ in range(TARGET)]
        self.z = [None] * C.WAVE     # MRTZ depth export per lane (R channel); the other MRTZ channels are not modelled

    def step(self, inst, nxt):
        if inst.fmt == "VINTRP":
            w = inst.words[0]
            op, vdst, a, chan = (w >> 16) & 3, (w >> 18) & 0xff, (w >> 10) & 0x3f, (w >> 8) & 3
            for lane in range(C.WAVE):
                if not (self.exec() >> lane) & 1:
                    continue
                if op in (0, 2):   # p1 / mov: the constant attribute (the barycentric terms are zero for a constant)
                    self.v[vdst][lane] = C.b32(attr(a, chan))
            if op == 3:
                raise C.Unsupported("vintrp op3")
            return None
        if inst.fmt == "EXP":
            w0, w1 = inst.words[0], inst.words[1]
            en, target, compr = w0 & 0xf, (w0 >> 4) & 0x3f, (w0 >> 10) & 1
            if target == 9:
                return None
            srcs = [(w1 >> (8 * k)) & 0xff for k in range(4)]
            if target == 8:
                if compr or en & 0xe:
                    raise C.Unsupported("MRTZ export with stencil/mask/alpha or compressed")
                for lane in range(C.WAVE):
                    if (self.exec() >> lane) & 1 and en & 1:
                        self.z[lane] = self.v[srcs[0]][lane]
                return None
            if target > 7:
                raise C.Unsupported("export target %d" % target)
            for lane in range(C.WAVE):
                if not (self.exec() >> lane) & 1:
                    continue
                vals = {}
                if compr:
                    for k in (0, 1):
                        if (en >> (2 * k)) & 3:   # compressed: EN bits 1:0 enable the first pair (R,G), bits 3:2 the second (B,A)
                            x = self.v[srcs[k]][lane]
                            vals[2 * k], vals[2 * k + 1] = C.b32(C.f16(x & 0xffff)), C.b32(C.f16(x >> 16))
                else:
                    for k in range(4):
                        if (en >> k) & 1:
                            vals[k] = self.v[srcs[k]][lane]
                for c, v in vals.items():
                    self.tg[target][lane][c] = v
                    self.wr[target][lane][c] = True
            return None
        return super().step(inst, nxt)

    def lane_op(self, n, a, inst, f, lane, vdst, w):
        if n == "v_cvt_pkrtz_f16_f32":
            return f16_rtz(C.f32(a[0])) | (f16_rtz(C.f32(a[1])) << 16)
        return super().lane_op(n, a, inst, f, lane, vdst, w)


def attrs(idx):
    d = open(R.ROOT + f"bins_all/{idx}.bin", "rb").read()
    ins, _ = C.rdna2.sweep(d, 0, len(d))
    a, f = [], []
    for i in ins:
        if i.fmt.startswith("VINTRP"):
            a.append((i.words[0] >> 10) & 0x3f)
        elif i.name in ("v_interp_p1ll_f16", "v_interp_p1lv_f16", "v_interp_p2_f16"):
            x = i.words[1] & 0x3f
            a.append(x); f.append(x)
    return (max(a) + 1 if a else 0, sum(1 << x for x in set(f)))


def one(args, code_override=None):
    idx, wave = args
    d = tempfile.mkdtemp(prefix=f"ps{idx}_")
    try:
        lines, info = R.build(idx, wave, d, wave, "float")
    except R.Unsupported as e:
        return {"idx": idx, "outcome": "SKIP", "detail": str(e)}
    count, fp16 = attrs(idx)
    code = code_override if code_override is not None else open(R.ROOT + f"bins_all/{idx}.bin", "rb").read()
    if fp16:
        return {"idx": idx, "outcome": "SKIP", "detail": "16-bit interpolants"}
    lines = [l for l in lines if l.split()[0] not in ("groups", "threads", "lds")]
    # export format per target follows the code: compressed exports mean FP16_ABGR (mode 4), plain ones 32_ABGR (mode 9)
    modes = {}
    code = code_override if code_override is not None else open(R.ROOT + f"bins_all/{idx}.bin", "rb").read()
    if code_override is not None:
        cp = os.path.join(d, "patched.bin")
        open(cp, "wb").write(code)
        lines = [("code " + cp.replace("\\", "/")) if l.startswith("code ") else l for l in lines]
    for i in C.rdna2.sweep(code, 0, len(code))[0]:
        if i.fmt == "EXP" and ((i.words[0] >> 4) & 0x3f) < 8:
            modes.setdefault((i.words[0] >> 4) & 0x3f, set()).add((i.words[0] >> 10) & 1)
    if any(len(v) > 1 for v in modes.values()):
        return {"idx": idx, "outcome": "SKIP", "detail": "mixed compressed/plain exports to one target"}
    lines += ["export %d %d 0xe4" % (t, 4 if 1 in v else 9) for t, v in modes.items()]
    if any(i.fmt == "EXP" and ((i.words[0] >> 4) & 0x3f) == 8 for i in C.rdna2.sweep(code, 0, len(code))[0]):
        lines.append("depth")
    lines += ["stage ps", f"target {TARGET} {TARGET}", f"interp {count} 0", "inputaddr 2", "vs " + vertex_shader(count, d)]
    # the input VGPR set: the smallest candidate for which the CPU result does not depend on the junk in the undefined registers
    chosen = None
    for ia in INPUT_ADDRS:
        cpu = []
        try:
            for junk in (1, 2):
                regions, sgpr, images = CC.scenario_memory(lines)
                mem = C.Memory()
                guest = {rid: 0x10000000 + 0x1000000 * k for k, rid in enumerate(sorted(regions))}
                for rid, dd in regions.items():
                    mem.add(guest[rid], dd)
                wave_ = PSWave(code, mem, sgpr, junk, ia)
                wave_.images = images
                wave_.run()
                cpu.append((wave_, regions, images))
        except C.Unsupported as e:
            return {"idx": idx, "outcome": "SKIP", "detail": str(e)[:100]}
        except (KeyError, IndexError, ValueError, struct.error, OverflowError, ZeroDivisionError) as e:
            return {"idx": idx, "outcome": "SKIP", "detail": "interpreter error " + type(e).__name__ + " " + str(e)[:60]}
        w1, w2 = cpu[0][0], cpu[1][0]
        same = (w1.tg == w2.tg and w1.wr == w2.wr and w1.z == w2.z and all(bytes(cpu[0][1][r]) == bytes(cpu[1][1][r]) for r in cpu[0][1])
                and all(cpu[0][2][i]["data"] == cpu[1][2][i]["data"] for i in cpu[0][2]))
        if same:
            chosen = ia
            break
    if chosen is None:
        return {"idx": idx, "outcome": "SKIP", "detail": "depends on unmodelled input registers"}
    lines = [l for l in lines if not l.startswith("inputaddr ")] + ["inputaddr %x" % chosen]
    wave_, regions, images = cpu[0]
    sc = os.path.join(d, "scenario.txt")
    open(sc, "w").write("\n".join(lines) + "\n")
    try:
        p = subprocess.run([R.EXEC, sc], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"idx": idx, "outcome": "GPU_FAIL", "detail": "timeout"}
    out = p.stdout.strip().splitlines()
    if out[-1:] != ["RAN"]:
        return {"idx": idx, "outcome": "GPU_FAIL", "detail": (out[-1] if out else p.stderr[-100:])[:150]}
    bad = []
    nwritten = 0
    for t in range(TARGET):
        gpu = open(f"{d}/out/target{t}.bin", "rb").read()
        for px in range(TARGET * TARGET):
            lane = PIXEL_LANE[(px % TARGET, px // TARGET)]
            for c in range(4):
                if not w1.wr[t][lane][c]:
                    continue
                nwritten += 1
                a = struct.unpack_from('<I', gpu, 16 * px + 4 * c)[0]
                b = w1.tg[t][lane][c]
                if a != b and not (C.isnan(a) and C.isnan(b)):
                    bad.append((t, px, c, a, b))
    if any(z is not None for z in w1.z):
        gpu = open(f"{d}/out/depth.bin", "rb").read()
        for px in range(TARGET * TARGET):
            z = w1.z[PIXEL_LANE[(px % TARGET, px // TARGET)]]
            if z is None:
                continue
            x = C.f32(z)
            want = C.b32(0.0 if x != x else min(max(x, 0.0), 1.0))
            nwritten += 1
            a = struct.unpack_from('<I', gpu, 4 * px)[0]
            if a != want:
                bad.append((8, px, 0, a, want))
    for r in info["data"]:
        gpu = open(f"{d}/out/region{r}.bin", "rb").read()
        if gpu != bytes(regions[r]):
            for k in range(0, len(gpu), 4):
                a, b = struct.unpack_from('<I', gpu, k)[0], struct.unpack_from('<I', bytes(regions[r]), k)[0]
                if a != b:
                    bad.append((100 + r, k, 0, a, b))
    rec = {"idx": idx, "outcome": "MISMATCH" if bad else "MATCH", "exported_channels": nwritten, "steps": wave_.steps}
    if bad:
        rec["ndiff"] = len(bad)
        rec["first"] = [(t, px, c, hex(a), hex(b)) for t, px, c, a, b in bad[:4]]
    return rec


def probe(idx, pc, regs, wave=64):
    """cut the shader at byte offset pc and export the raw 32-bit values of up to four VGPRs (target 0, mode 9): both sides run the cut code,
    so the first pc where the GPU and the CPU disagree locates a recompiler or reference bug"""
    code = open(R.ROOT + f"bins_all/{idx}.bin", "rb").read()
    regs = list(regs) + [0] * (4 - len(regs))
    exp = struct.pack("<II", 0xF8000000 | 0xf | (0 << 4), regs[0] | regs[1] << 8 | regs[2] << 16 | regs[3] << 24)
    cut = code[:pc] + exp + struct.pack("<I", 0xBF810000)
    return one((idx, wave), cut)


def main():
    argv = sys.argv[1:]
    opt = lambda name, default: argv[argv.index(name) + 1] if name in argv else default
    import csv, collections
    mb = int(opt("--max-bytes", 1500))
    wave = int(opt("--wave", 64))
    rows = [r for r in csv.DictReader(open(R.ROOT + "manifest_all.csv")) if r["stage"] == "PS" and int(r["bytes"]) <= mb]
    ids = opt("--ids", "").split(",") if "--ids" in argv else [r["idx"] for r in rows]
    ids = [i for i in ids if os.path.exists(R.ROOT + f"binds_all/{i}.bind")]
    # build the vertex shaders serially first (the cache is not thread safe)
    for i in ids:
        try:
            vertex_shader(attrs(i)[0], "")
        except Exception:
            pass
    with cf.ThreadPoolExecutor(int(opt("--workers", 6))) as ex:
        res = list(ex.map(one, [(i, wave) for i in ids]))
    json.dump(res, open(argv[0], "w"), indent=1)
    print(collections.Counter(r["outcome"] for r in res))
    print("mismatch", [(r["idx"], r["first"][:1]) for r in res if r["outcome"] == "MISMATCH"][:20])
    print(collections.Counter(r["detail"].split(" (")[0][:60] for r in res if r["outcome"] in ("SKIP", "GPU_FAIL")).most_common(14))


if __name__ == "__main__":
    main()
