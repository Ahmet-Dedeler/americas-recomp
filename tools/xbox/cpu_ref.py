"""A small RDNA2 interpreter used as an independent reference for the recompiler.

It runs ONE wave64 workgroup of a compute shader on the CPU over the same memory image that tools/xbox/exec.cpp builds from
a scenario, and compares the resulting buffers with what the recompiled SPIR-V did on the GPU.

Mnemonics and instruction formats come from out_disasm/rdna2.py (the 'name' and 'fmt' fields). Operand fields are decoded
here from the raw instruction words, because the disassembler's operand text is not faithful for VOP3 (compare destinations,
modifiers). Anything not implemented raises Unsupported, so a result is either a real comparison or an explicit skip.

Assumptions that are NOT checked against hardware (the reference shares them with the harness):
  * buffer *_format_* accesses treat the components as raw 32-bit values (the harness V# format is 32_32_32_32_FLOAT)
  * V# OOB_SELECT = 1 (harness descriptor): an index >= num_records reads as 0 and its stores are dropped; byte offsets are unchecked
  * floating-point transcendentals are compared with a small ULP tolerance (hardware rcp/exp/log are approximate)
"""
import math, os, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "rpf9shaders", "out_disasm"))
sys.path.insert(0, r"E:/Programming/rpf9shaders/out_disasm")
import rdna2

M32 = 0xffffffff
WAVE = 64
RECOMPILER_DECODER = r"E:/Programming/anyps5_src/repo/core/shader/recompiler/RdnaDecoder/src/RdnaVectorOpDecoder.cpp"


def _vopc_names():
    """VOPC opcode -> mnemonic, taken from the recompiler's decoder table. The Xbox shaders use this (GCN-style) numbering;
    out_disasm/rdna2.py names the same codes with the PC gfx10 table, which is wrong for them (0xC2 is v_cmp_eq_u32, not v_cmpx_eq_i32)."""
    import re
    text = open(RECOMPILER_DECODER, encoding="utf-8").read()
    body = text[text.index("vopcOpcodes[] = {"):]
    body = body[:body.index("};")]
    names = {}
    for code, op in re.findall(r"\{0x([0-9a-f]+)u, RdnaOpcode::(\w+)\}", body):
        tok = re.findall(r"[A-Z][a-z0-9]*", op)
        if len(tok) >= 4 and tok[0] == "V" and tok[1] in ("Cmp", "Cmpx"):
            names[int(code, 16)] = "v_%s_%s_%s" % (tok[1].lower(), tok[2].lower(), tok[3].lower())
    return names


VOPC_NAMES = _vopc_names()


class Unsupported(Exception):
    pass


def f32(b):
    return struct.unpack('<f', struct.pack('<I', b & M32))[0]


def b32(x):
    try:
        return struct.unpack('<I', struct.pack('<f', x))[0]
    except OverflowError:
        return 0x7f800000 if x > 0 else 0xff800000


def f16(b):
    return struct.unpack('<e', struct.pack('<H', b & 0xffff))[0]


def s32(x):
    x &= M32
    return x - (1 << 32) if x & 0x80000000 else x


def isnan(b):
    return (b & 0x7f800000) == 0x7f800000 and (b & 0x7fffff) != 0


class Memory:
    def __init__(self):
        self.regions = []   # [guest_base, bytearray]

    def add(self, base, data):
        self.regions.append([base, data])

    def find(self, addr, n):
        for base, d in self.regions:
            if base <= addr and addr + n <= base + len(d):
                return d, addr - base
        return None, 0

    def read(self, addr, n):
        d, o = self.find(addr, n)
        if d is None:
            raise Unsupported("read of unmapped address 0x%x" % addr)
        return bytes(d[o:o + n])

    def write(self, addr, data):
        d, o = self.find(addr, len(data))
        if d is None:
            raise Unsupported("write to unmapped address 0x%x" % addr)
        d[o:o + len(data)] = data

    def u32(self, addr):
        return struct.unpack('<I', self.read(addr, 4))[0]


FLOAT_APPROX = {"v_rcp_f32", "v_rcp_iflag_f32", "v_sqrt_f32", "v_exp_f32", "v_log_f32", "v_rsq_f32"}


class Wave:
    def __init__(self, code, mem, sgpr_init):
        self.mem = mem
        self.s = [0] * 128
        for k, v in sgpr_init.items():
            self.s[k] = v & M32
        self.v = [[0] * WAVE for _ in range(256)]
        for lane in range(WAVE):
            self.v[0][lane] = lane
        self.s[126], self.s[127] = M32, M32      # exec = all 64 lanes
        self.scc = 0
        self.lds = bytearray(65536)
        self.approx = False
        self.images = {}    # id -> {w, h, data (floats), written}
        self.by_off = {}
        off = 0
        while off < len(code):
            i = rdna2.decode(code, off)
            # VOP2 madmk/madak/fmamk/fmaak carry a literal in the following dword; rdna2.py decodes them as 4-byte instructions
            # and then treats the literal as an instruction of its own, so merge the literal into the instruction here
            if i.fmt == "VOP2" and len(i.words) == 1 and ((i.words[0] >> 25) & 0x3f) in (0x20, 0x21, 0x2c, 0x2d) and (i.words[0] >> 31) == 0:
                i.words = [i.words[0], int.from_bytes(code[i.off + 4:i.off + 8], "little")]
                i.size = 8
                i.name = {0x20: "v_madmk_f32", 0x21: "v_madak_f32", 0x2c: "v_fmamk_f32", 0x2d: "v_fmaak_f32"}[(i.words[0] >> 25) & 0x3f]
            self.by_off[i.off] = i
            off += i.size
        self.code = code
        self.steps = 0

    # ---- scalar operand access -------------------------------------------------------------------------------
    def lit(self, inst):
        # the literal constant is the last word of the instruction (the third word of a VOP3)
        return inst.words[-1] if len(inst.words) > 1 else 0

    def sread(self, code, inst, wide=False):
        """read an 8/9-bit scalar source; wide = 64-bit"""
        if code <= 105 or code in (106, 107, 124, 126, 127):
            if wide:
                return self.s[code] | (self.s[code + 1] << 32)
            return self.s[code]
        if code == 125:
            return 0
        if code == 128:
            return 0
        if 129 <= code <= 192:
            v = code - 128
        elif 193 <= code <= 208:
            v = -(code - 192)
        elif code == 255:
            v = self.lit(inst)
            return v & (M32 if not wide else M32)
        else:
            raise Unsupported("scalar source %d" % code)
        return v & ((1 << 64) - 1 if wide else M32)

    def swrite(self, code, value, wide=False):
        if code == 125:
            return
        if code > 127:
            raise Unsupported("scalar dest %d" % code)
        self.s[code] = value & M32
        if wide:
            self.s[code + 1] = (value >> 32) & M32

    # ---- vector operand access -------------------------------------------------------------------------------
    def vsrc(self, code, inst, lane):
        if code >= 256:
            return self.v[code - 256][lane]
        if code == 255:
            return self.lit(inst)
        if code in (240, 241, 242, 243, 244, 245, 246, 247):
            return b32({240: 0.5, 241: -0.5, 242: 1.0, 243: -1.0, 244: 2.0, 245: -2.0, 246: 4.0, 247: -4.0}[code])
        if code == 248:
            return b32(0.15915494309189535)
        return self.sread(code, inst)

    def exec(self):
        return self.s[126] | (self.s[127] << 32)

    def set_exec(self, m):
        self.s[126], self.s[127] = m & M32, (m >> 32) & M32

    # ---- run -------------------------------------------------------------------------------------------------
    def run(self, limit=2_000_000):
        pc = 0
        while True:
            self.steps += 1
            if self.steps > limit:
                raise Unsupported("step limit")
            inst = self.by_off.get(pc)
            if inst is None:
                raise Unsupported("pc 0x%x is not an instruction" % pc)
            nxt = pc + inst.size
            tgt = self.step(inst, nxt)
            if tgt == "end":
                return
            pc = nxt if tgt is None else tgt

    def step(self, inst, nxt):
        f = inst.fmt
        w = inst.words[0]
        n = inst.name
        if f == "SOPP":
            return self.sopp(inst, w, n, nxt)
        if f == "SOP1":
            return self.sop1(inst, w, n)
        if f == "SOP2":
            return self.sop2(inst, w, n)
        if f == "SOPK":
            return self.sopk(inst, w, n)
        if f == "SOPC":
            return self.sopc(inst, w, n)
        if f == "SMEM":
            return self.smem(inst, w, n)
        if f in ("VOP1", "VOP2", "VOPC", "VOP3"):
            return self.valu(inst, w, n, f)
        if f == "MUBUF":
            return self.mubuf(inst, w, n)
        if f in ("MIMG", "MIMG+NSA1", "MIMG+NSA3", "MIMG+NSA2"):
            return self.mimg(inst, w, n)
        if f == "DS":
            return self.ds(inst, w, n)
        raise Unsupported("format %s (%s)" % (f, n))

    # ---- SALU ------------------------------------------------------------------------------------------------
    def sopp(self, inst, w, n, nxt):
        simm = w & 0xffff
        if n in ("s_nop", "s_waitcnt", "s_waitcnt_vscnt", "s_inst_prefetch", "s_barrier", "s_sleep", "s_clause", "s_code_end", "s_waitcnt_vmcnt", "s_waitcnt_lgkmcnt", "s_waitcnt_expcnt"):
            return None
        if n == "s_endpgm":
            return "end"
        off = (simm - 0x10000 if simm & 0x8000 else simm) * 4
        tgt = nxt + off
        if n == "s_branch":
            return tgt
        if n == "s_cbranch_scc0":
            return tgt if self.scc == 0 else None
        if n == "s_cbranch_scc1":
            return tgt if self.scc == 1 else None
        if n == "s_cbranch_execz":
            return tgt if self.exec() == 0 else None
        if n == "s_cbranch_execnz":
            return tgt if self.exec() != 0 else None
        vcc = self.s[106] | (self.s[107] << 32)
        if n == "s_cbranch_vccz":
            return tgt if vcc == 0 else None
        if n == "s_cbranch_vccnz":
            return tgt if vcc != 0 else None
        raise Unsupported(n)

    def sop1(self, inst, w, n):
        sdst, ssrc = (w >> 16) & 0x7f, w & 0xff
        wide = n.endswith("_b64")
        a = self.sread(ssrc, inst, wide)
        mask = (1 << 64) - 1 if wide else M32
        if n in ("s_mov_b32", "s_mov_b64"):
            self.swrite(sdst, a, wide)
        elif n in ("s_not_b32", "s_not_b64"):
            r = ~a & mask
            self.swrite(sdst, r, wide)
            self.scc = int(r != 0)
        elif n == "s_brev_b32":
            self.swrite(sdst, int(format(a, "032b")[::-1], 2))
        elif n == "s_ff1_i32_b64":
            self.swrite(sdst, (a & -a).bit_length() - 1 if a else M32)
        elif n == "s_bcnt1_i32_b64":
            r = bin(a).count("1")
            self.swrite(sdst, r)
            self.scc = int(r != 0)
        elif n in ("s_and_saveexec_b32", "s_and_saveexec_b64"):
            old = self.exec() if wide else self.s[126]
            self.swrite(sdst, old, wide)
            new = a & old
            self.set_exec(new if wide else (self.exec() & ~M32 | new))
            self.scc = int(new != 0)
        else:
            raise Unsupported(n)

    def sop2(self, inst, w, n):
        sdst, s1, s0 = (w >> 16) & 0x7f, (w >> 8) & 0xff, w & 0xff
        wide = n.endswith("_b64")
        a, b = self.sread(s0, inst, wide), self.sread(s1, inst, wide)
        mask = (1 << 64) - 1 if wide else M32
        if n in ("s_and_b32", "s_and_b64", "s_or_b32", "s_or_b64", "s_xor_b32", "s_xor_b64", "s_andn2_b32", "s_andn2_b64", "s_orn2_b32", "s_orn2_b64", "s_nand_b32", "s_nand_b64", "s_nor_b32", "s_nor_b64"):
            op = n.split("_")[1]
            r = {"and": a & b, "or": a | b, "xor": a ^ b, "andn2": a & ~b, "orn2": a | ~b, "nand": ~(a & b), "nor": ~(a | b)}[op] & mask
            self.swrite(sdst, r, wide)
            self.scc = int(r != 0)
            return
        if n == "s_add_i32":
            r = (s32(a) + s32(b))
            self.scc = int(not -(1 << 31) <= r < (1 << 31))
            self.swrite(sdst, r)
        elif n == "s_sub_i32":
            r = (s32(a) - s32(b))
            self.scc = int(not -(1 << 31) <= r < (1 << 31))
            self.swrite(sdst, r)
        elif n == "s_add_u32":
            self.scc = int(a + b > M32)
            self.swrite(sdst, a + b)
        elif n == "s_sub_u32":
            self.scc = int(b > a)
            self.swrite(sdst, a - b)
        elif n == "s_lshl_b32":
            r = (a << (b & 31)) & M32
            self.swrite(sdst, r)
            self.scc = int(r != 0)
        elif n == "s_lshr_b32":
            r = a >> (b & 31)
            self.swrite(sdst, r)
            self.scc = int(r != 0)
        elif n == "s_ashr_i32":
            r = (s32(a) >> (b & 31)) & M32
            self.swrite(sdst, r)
            self.scc = int(r != 0)
        elif n == "s_min_u32":
            self.swrite(sdst, min(a, b)); self.scc = int(a < b)
        elif n == "s_max_u32":
            self.swrite(sdst, max(a, b)); self.scc = int(a > b)
        elif n == "s_min_i32":
            self.swrite(sdst, min(s32(a), s32(b))); self.scc = int(s32(a) < s32(b))
        elif n == "s_max_i32":
            self.swrite(sdst, max(s32(a), s32(b))); self.scc = int(s32(a) > s32(b))
        elif n == "s_mul_i32":
            self.swrite(sdst, a * b)
        elif n == "s_mul_hi_u32":
            self.swrite(sdst, (a * b) >> 32)
        elif n in ("s_bfe_u32", "s_bfe_i32"):
            off, width = b & 31, (b >> 16) & 0x7f
            r = (a >> off) & ((1 << width) - 1) if width else 0
            if n == "s_bfe_i32" and width and r & (1 << (width - 1)):
                r -= 1 << width
            self.swrite(sdst, r)
            self.scc = int((r & M32) != 0)
        elif n in ("s_cselect_b32", "s_cselect_b64"):
            self.swrite(sdst, a if self.scc else b, wide)
        else:
            raise Unsupported(n)

    def sopk(self, inst, w, n):
        simm, sdst = w & 0xffff, (w >> 16) & 0x7f
        sv = simm - 0x10000 if simm & 0x8000 else simm
        if n == "s_movk_i32":
            self.swrite(sdst, sv)
        elif n == "s_addk_i32":
            r = s32(self.s[sdst]) + sv
            self.scc = int(not -(1 << 31) <= r < (1 << 31))
            self.swrite(sdst, r)
        elif n.startswith("s_cmpk_"):
            _, _, rel, ty = n.split("_")
            a = self.s[sdst]
            b = simm if ty == "u32" else sv
            if ty == "i32":
                a = s32(a)
            self.scc = int(CMP[rel](a, b))
        else:
            raise Unsupported(n)

    def sopc(self, inst, w, n):
        s0, s1 = w & 0xff, (w >> 8) & 0xff
        a, b = self.sread(s0, inst), self.sread(s1, inst)
        _, _, rel, ty = n.split("_")
        if ty == "i32":
            a, b = s32(a), s32(b)
        self.scc = int(CMP[rel](a, b))

    def smem(self, inst, w, n):
        sbase, sdata = (w & 0x3f) * 2, (w >> 6) & 0x7f
        w1 = inst.words[1]
        off = w1 & 0x1fffff
        if off & 0x100000:
            off -= 0x200000
        soff = (w1 >> 25) & 0x7f
        if soff != 125:
            off += self.s[soff]
        count = {"x2": 2, "x4": 4, "x8": 8, "x16": 16}.get(n.rsplit("dword", 1)[1], 1)
        if n.startswith("s_load_dword"):
            addr = self.s[sbase] | (self.s[sbase + 1] << 32)
        elif n.startswith("s_buffer_load_dword"):
            addr = self.s[sbase] | ((self.s[sbase + 1] & 0xffff) << 32)
        else:
            raise Unsupported(n)
        for k in range(count):
            self.s[sdata + k] = self.mem.u32(addr + off + 4 * k)

    # ---- VALU ------------------------------------------------------------------------------------------------
    def valu(self, inst, w, n, f):
        # gather operand codes
        neg = absm = 0
        clamp = omod = 0
        sdst = None
        if f == "VOP1":
            src = [w & 0x1ff]; vdst = (w >> 17) & 0xff
        elif f == "VOP2":
            src = [w & 0x1ff, 256 + ((w >> 9) & 0xff)]; vdst = (w >> 17) & 0xff
        elif f == "VOPC":
            src = [w & 0x1ff, 256 + ((w >> 9) & 0xff)]; vdst = None
        else:
            w1 = inst.words[1]
            vdst = w & 0xff
            absm, clamp = (w >> 8) & 7, (w >> 15) & 1
            src = [w1 & 0x1ff, (w1 >> 9) & 0x1ff, (w1 >> 18) & 0x1ff]
            omod, neg = (w1 >> 27) & 3, (w1 >> 29) & 7
            if omod:
                raise Unsupported("omod on " + n)
            if (w >> 11) & 0xf:
                raise Unsupported("op_sel on " + n)
        if f == "VOPC":
            n = VOPC_NAMES.get((w >> 17) & 0xff) or "?"
        elif f == "VOP3" and ((w >> 16) & 0x3ff) <= 0xff:
            n = VOPC_NAMES.get((w >> 16) & 0x3ff) or "?"
        if n == "?":
            raise Unsupported("VOPC opcode")
        isf = "_f32" in n and "cvt" not in n
        is_cmp = n.startswith("v_cmp")
        lanes_exec = self.exec()
        res_mask = 0
        for lane in range(WAVE):
            act = (lanes_exec >> lane) & 1
            if not act and not (is_cmp and not n.startswith("v_cmpx")):
                continue   # cmp (non-x) writes 0 for inactive lanes in the mask; handled below
            a = [self.vsrc(c, inst, lane) if i < len(src) else 0 for i, c in enumerate(src)]
            if f == "VOP3":
                for i in range(len(a)):
                    if absm >> i & 1:
                        a[i] &= 0x7fffffff
                    if neg >> i & 1:
                        a[i] ^= 0x80000000
            if is_cmp:
                if not act:
                    continue
                if self.cmp(n, a[0], a[1]):
                    res_mask |= 1 << lane
                continue
            r = self.lane_op(n, a, inst, f, lane, vdst, w)
            if r is None:
                continue
            if isinstance(r, tuple):   # (value, carry)
                r, carry = r
                res_mask |= carry << lane
            if clamp and ("_f32" in n):
                x = f32(r)
                r = b32(min(max(x, 0.0), 1.0)) if not math.isnan(x) else 0
            if vdst is None:
                raise Unsupported("no vdst for " + n)
            self.v[vdst][lane] = r & M32
        if n in ("v_readfirstlane_b32",):
            return
        if is_cmp:
            if n.startswith("v_cmpx"):
                self.set_exec(res_mask)   # RDNA2: v_cmpx writes EXEC only (VCC is not written); a VOP3 form also writes its explicit SGPR destination
                if f == "VOP3" and vdst not in (125, 126):
                    self.s[vdst], self.s[vdst + 1] = res_mask & M32, (res_mask >> 32) & M32
            else:
                dst = 106 if f == "VOPC" else vdst
                self.s[dst], self.s[dst + 1] = res_mask & M32, (res_mask >> 32) & M32
        elif "_co_" in n:
            dst = (w >> 8) & 0x7f
            self.s[dst], self.s[dst + 1] = res_mask & M32, (res_mask >> 32) & M32

    def cmp(self, n, a, b):
        parts = n.replace("v_cmpx_", "").replace("v_cmp_", "").split("_")
        rel, ty = parts[0], parts[1]
        if rel not in CMP:
            raise Unsupported(n)
        if ty == "f32":
            x, y = f32(a), f32(b)
        elif ty == "f16":
            x, y = f16(a), f16(b)
        elif ty == "i32":
            x, y = s32(a), s32(b)
        elif ty == "u32":
            x, y = a, b
        else:
            raise Unsupported(n)
        if ty.startswith("f") and (math.isnan(x) or math.isnan(y)):
            return rel in ("u", "neq", "nlt", "nle", "ngt", "nge", "nlg")
        return CMP[rel](x, y)

    def lane_op(self, n, a, inst, f, lane, vdst, w):
        A = a[0] if a else 0
        B = a[1] if len(a) > 1 else 0
        C = a[2] if len(a) > 2 else 0
        if n == "v_nop":
            return None
        if n == "v_mov_b32":
            return A
        if n == "v_readfirstlane_b32":
            first = (self.exec() & -self.exec()).bit_length() - 1 if self.exec() else 0
            if lane == first or self.exec() == 0:
                self.s[vdst if f != "VOP1" else vdst] = A   # SGPR destination (same field)
            return None
        fl = {
            "v_add_f32": lambda: b32(f32(A) + f32(B)),
            "v_sub_f32": lambda: b32(f32(A) - f32(B)),
            "v_subrev_f32": lambda: b32(f32(B) - f32(A)),
            "v_mul_f32": lambda: b32(f32(A) * f32(B)),
            "v_mul_legacy_f32": lambda: 0 if (f32(A) == 0 or f32(B) == 0) else b32(f32(A) * f32(B)),
            "v_min_f32": lambda: b32(min(f32(A), f32(B))),
            "v_max_f32": lambda: b32(max(f32(A), f32(B))),
            "v_fma_f32": lambda: b32(math.fma(f32(A), f32(B), f32(C))),
            "v_fmac_f32": lambda: b32(math.fma(f32(A), f32(B), f32(self.v[vdst][lane]))),
            "v_madak_f32": lambda: b32(f32(b32(f32(A) * f32(B))) + f32(self.lit(inst))),
            "v_madmk_f32": lambda: b32(f32(b32(f32(A) * f32(self.lit(inst)))) + f32(B)),
            "v_fmaak_f32": lambda: b32(math.fma(f32(A), f32(B), f32(self.lit(inst)))),
            "v_fmamk_f32": lambda: b32(math.fma(f32(A), f32(self.lit(inst)), f32(B))),
            "v_max3_f32": lambda: b32(max(f32(A), f32(B), f32(C))),
            "v_min3_f32": lambda: b32(min(f32(A), f32(B), f32(C))),
            "v_med3_f32": lambda: b32(sorted((f32(A), f32(B), f32(C)))[1]),
            "v_ceil_f32": lambda: b32(float(math.ceil(f32(A)))) if math.isfinite(f32(A)) else A,
            "v_floor_f32": lambda: b32(float(math.floor(f32(A)))) if math.isfinite(f32(A)) else A,
            "v_cvt_f32_u32": lambda: b32(float(A)),
            "v_cvt_f32_i32": lambda: b32(float(s32(A))),
            "v_cvt_f32_ubyte0": lambda: b32(float(A & 0xff)),
            "v_cvt_f32_ubyte1": lambda: b32(float((A >> 8) & 0xff)),
            "v_cvt_f32_ubyte2": lambda: b32(float((A >> 16) & 0xff)),
            "v_cvt_f32_ubyte3": lambda: b32(float((A >> 24) & 0xff)),
            "v_cvt_u32_f32": lambda: 0 if math.isnan(f32(A)) else min(max(int(f32(A)), 0), M32) if math.isfinite(f32(A)) else (M32 if f32(A) > 0 else 0),
            "v_cvt_i32_f32": lambda: 0 if math.isnan(f32(A)) else (min(max(int(f32(A)), -(1 << 31)), (1 << 31) - 1) if math.isfinite(f32(A)) else ((1 << 31) - 1 if f32(A) > 0 else -(1 << 31))) & M32,
        }
        if n in FLOAT_APPROX:
            self.approx = True
            x = f32(A)
            if n in ("v_rcp_f32", "v_rcp_iflag_f32"):
                return b32(math.copysign(math.inf, x)) if x == 0 else (0 if math.isinf(x) else b32(1.0 / x))
            if n == "v_sqrt_f32":
                return b32(math.sqrt(x)) if x >= 0 else 0x7fc00000
            if n == "v_exp_f32":
                try:
                    return b32(2.0 ** x)
                except OverflowError:
                    return 0x7f800000
            if n == "v_log_f32":
                return b32(math.log2(x)) if x > 0 else (0xff800000 if x == 0 else 0x7fc00000)
            if n == "v_rsq_f32":
                return b32(1.0 / math.sqrt(x)) if x > 0 else 0x7fc00000
        if n in fl:
            r = fl[n]()
            return r
        il = {
            "v_and_b32": lambda: A & B, "v_or_b32": lambda: A | B, "v_xor_b32": lambda: A ^ B,
            "v_or3_b32": lambda: A | B | C,
            "v_lshlrev_b32": lambda: B << (A & 31), "v_lshrrev_b32": lambda: B >> (A & 31),
            "v_ashrrev_i32": lambda: s32(B) >> (A & 31),
            "v_add_nc_u32": lambda: A + B, "v_sub_nc_u32": lambda: A - B, "v_subrev_nc_u32": lambda: B - A,
            "v_min_u32": lambda: min(A, B), "v_max_u32": lambda: max(A, B),
            "v_min_i32": lambda: min(s32(A), s32(B)), "v_max_i32": lambda: max(s32(A), s32(B)),
            "v_mul_lo_u32": lambda: A * B, "v_mul_hi_u32": lambda: (A * B) >> 32,
            "v_lshl_add_u32": lambda: (A << (B & 31)) + C,
            "v_add3_u32": lambda: A + B + C,
            "v_add_lshl_u32": lambda: (A + B) << (C & 31),
            "v_lshl_or_b32": lambda: (A << (B & 31)) | C,
            "v_and_or_b32": lambda: (A & B) | C,
            "v_xad_u32": lambda: (A ^ B) + C,
            "v_mad_u32_u24": lambda: (A & 0xffffff) * (B & 0xffffff) + C,
            "v_mul_u32_u24": lambda: (A & 0xffffff) * (B & 0xffffff),
            "v_bfe_u32": lambda: (A >> (B & 31)) & ((1 << (C & 31)) - 1),
            "v_med3_u32": lambda: sorted((A, B, C))[1],
            "v_med3_i32": lambda: sorted((s32(A), s32(B), s32(C)))[1],
            "v_mbcnt_lo_u32_b32": lambda: bin(A & ((1 << lane) - 1) & M32 if lane < 32 else A & M32).count("1") + B,
            "v_mbcnt_hi_u32_b32": lambda: bin(A & ((1 << (lane - 32)) - 1) if lane >= 32 else 0).count("1") + B,
        }
        if n in il:
            return il[n]() & M32
        if n == "v_cndmask_b32":
            mask = (self.s[106] | (self.s[107] << 32)) if f in ("VOP1", "VOP2") else (self.sread(a_code(inst, 2), inst, True))
            return B if (mask >> lane) & 1 else A
        if n in ("v_add_co_u32", "v_sub_co_u32"):
            r = A + B if n == "v_add_co_u32" else A - B
            return (r & M32, int(r > M32 or r < 0))
        raise Unsupported(n)

    # ---- memory ----------------------------------------------------------------------------------------------
    def vsharp(self, base_reg):
        w0, w1, w2 = self.s[base_reg], self.s[base_reg + 1], self.s[base_reg + 2]
        return w0 | ((w1 & 0xffff) << 32), (w1 >> 16) & 0x3fff, w2

    def mubuf(self, inst, w, n):
        if (w >> 25) & 1:
            # MUBUF opcodes are 8 bits wide (the recompiler decodes bit 25 as bit 7); rdna2.py drops it and names the D16 forms
            # (0x80+, half-precision data in packed registers) as their 32-bit twins
            raise Unsupported("MUBUF D16 opcode 0x%x" % (((w >> 18) & 0x7f) | 0x80))
        w1 = inst.words[1]
        off = w & 0xfff
        offen, idxen = (w >> 12) & 1, (w >> 13) & 1
        vaddr, vdata, srsrc, soff = w1 & 0xff, (w1 >> 8) & 0xff, ((w1 >> 16) & 0x1f) * 4, (w1 >> 24) & 0xff
        base, stride, nrec = self.vsharp(srsrc)
        soffv = self.sread(soff, inst)
        # register layout of the address: idx then offset when both are enabled
        lane_exec = self.exec()
        if n.startswith("buffer_load_dword") or n.startswith("buffer_load_format"):
            comps = {"x": 1, "xy": 2, "xyz": 3, "xyzw": 4}[n.rsplit("_", 1)[1]] if "format" in n else {"dword": 1, "dwordx2": 2, "dwordx3": 3, "dwordx4": 4}[n.rsplit("_", 1)[1]]
            store = False
        elif n.startswith("buffer_store_dword") or n.startswith("buffer_store_format"):
            comps = {"x": 1, "xy": 2, "xyz": 3, "xyzw": 4}[n.rsplit("_", 1)[1]] if "format" in n else {"dword": 1, "dwordx2": 2, "dwordx3": 3, "dwordx4": 4}[n.rsplit("_", 1)[1]]
            store = True
        elif n == "buffer_atomic_add":
            comps, store = 1, "atomic"
        else:
            raise Unsupported(n)
        fmt = "format" in n
        for lane in range(WAVE):
            if not (lane_exec >> lane) & 1:
                continue
            k = vaddr
            idx = voff = 0
            if idxen:
                idx = self.v[k][lane]; k += 1
            if offen:
                voff = self.v[k][lane]
            if idxen:
                if idx >= nrec:
                    inrange = False
                else:
                    inrange = True
                addr = base + idx * stride + voff + off + soffv
            else:
                inrange = True    # V# OOB_SELECT 1 (the harness descriptor): only the index is range checked, and idx is 0 here
                addr = base + voff + off + soffv
            if inrange and addr & 3:
                # a dword access at a byte address that is not 4-aligned: undefined for D3D buffers; the recompiler reads the
                # containing aligned dword, hardware may assemble it from two. No reference value, so do not guess.
                raise Unsupported("unaligned dword buffer access")
            if store is True:
                if inrange:
                    data = b"".join(struct.pack('<I', self.v[vdata + c][lane]) for c in range(comps))
                    self.mem.write(addr, data)
            elif store == "atomic":
                if inrange:
                    old = self.mem.u32(addr)
                    self.mem.write(addr, struct.pack('<I', (old + self.v[vdata][lane]) & M32))
                    if (w >> 14) & 1:   # glc: return the old value
                        self.v[vdata][lane] = old
            else:
                for c in range(comps):
                    self.v[vdata + c][lane] = self.mem.u32(addr + 4 * c) if inrange else 0
        # format loads of fewer than 4 components leave the rest to the shader; nothing more to do

    def mimg(self, inst, w, n):
        """2D RGBA32F images only (the harness image model): image_load, image_store, image_sample_lz, image_sample_l.
        Sampling is nearest with clamp-to-edge on normalised coordinates, like the harness sampler."""
        op = ((w >> 18) & 0x7f) | ((w & 1) << 7)
        nsa = (w >> 1) & 3
        dmask, dim = (w >> 8) & 0xf, (w >> 3) & 7
        w1 = inst.words[1]
        vaddr, vdata, srsrc = w1 & 0xff, (w1 >> 8) & 0xff, ((w1 >> 16) & 0x1f) * 4
        if (w1 >> 30) & 1 or (w1 >> 31) & 1:
            raise Unsupported("a16/d16 image access")
        if dim != 1:
            raise Unsupported("image dim %d" % dim)
        kinds = {0x00: ("load", 2), 0x08: ("store", 2), 0x27: ("sample", 2), 0x24: ("sample", 3)}
        if op not in kinds:
            raise Unsupported("MIMG opcode 0x%x" % op)
        kind, naddr = kinds[op]
        regs = [vaddr + i for i in range(naddr)]
        if nsa:
            regs = [vaddr]
            for word in inst.words[2:]:
                regs += [(word >> (8 * b)) & 0xff for b in range(4)]
            regs = regs[:naddr]
        elif nsa == 0 and False:
            pass
        words = [self.s[srsrc + k] for k in range(8)]
        base = (words[0] << 8) | ((words[1] & 0xff) << 40)
        img_id = (base - 0x7000000000) >> 24
        img = self.images.get(img_id) if base >= 0x7000000000 and (base - 0x7000000000) % 0x1000000 == 0 else None
        if img is None:
            raise Unsupported("image descriptor does not name a known image")
        wd, ht = img["w"], img["h"]
        comps = [c for c in range(4) if (dmask >> c) & 1]
        ex = self.exec()
        for lane in range(WAVE):
            if not (ex >> lane) & 1:
                continue
            a = [self.v[r][lane] for r in regs]
            if kind == "sample":
                u, v = f32(a[0]), f32(a[1])
                if not (math.isfinite(u) and math.isfinite(v)):
                    raise Unsupported("non-finite sample coordinate")
                x = min(max(math.floor(f32(b32(u * wd))), 0), wd - 1)
                y = min(max(math.floor(f32(b32(v * ht))), 0), ht - 1)
            else:
                x, y = s32(a[0]), s32(a[1])
                if not (0 <= x < wd and 0 <= y < ht):
                    raise Unsupported("image access out of bounds")
            texel = (y * wd + x) * 4
            if kind == "store":
                vals = [self.v[vdata + k][lane] for k in range(len(comps))]
                for c, val in zip(comps, vals):
                    key = (img_id, texel + c)
                    prev = img["written"].get(key)
                    if prev is not None and prev[0] != lane and prev[1] != val:
                        raise Unsupported("racing image stores")
                    img["written"][key] = (lane, val)
                    img["data"][texel + c] = val & M32     # raw bits: a float round trip would quiet signalling NaNs
            else:
                for k, c in enumerate(comps):
                    self.v[vdata + k][lane] = img["data"][texel + c]

    def ds(self, inst, w, n):
        o0, o1 = w & 0xff, (w >> 8) & 0xff
        off16 = w & 0xffff
        w1 = inst.words[1]
        addr_r, d0, d1, vdst = w1 & 0xff, (w1 >> 8) & 0xff, (w1 >> 16) & 0xff, (w1 >> 24) & 0xff
        ex = self.exec()
        for lane in range(WAVE):
            if not (ex >> lane) & 1:
                continue
            a = self.v[addr_r][lane]
            def rd(x): return struct.unpack_from('<I', self.lds, x)[0]
            def wr(x, v): struct.pack_into('<I', self.lds, x, v & M32)
            if n == "ds_write_b32":
                wr(a + off16, self.v[d0][lane])
            elif n == "ds_read_b32":
                self.v[vdst][lane] = rd(a + off16)
            elif n == "ds_write2_b32":
                wr(a + o0 * 4, self.v[d0][lane]); wr(a + o1 * 4, self.v[d1][lane])
            elif n == "ds_read2_b32":
                self.v[vdst][lane] = rd(a + o0 * 4); self.v[vdst + 1][lane] = rd(a + o1 * 4)
            elif n in ("ds_add_u32", "ds_max_u32", "ds_or_b32"):
                old = rd(a + off16)
                x = self.v[d0][lane]
                wr(a + off16, {"ds_add_u32": old + x, "ds_max_u32": max(old, x), "ds_or_b32": old | x}[n])
            else:
                raise Unsupported(n)


def a_code(inst, i):
    w1 = inst.words[1]
    return (w1 >> (9 * i)) & 0x1ff


CMP = {"eq": lambda a, b: a == b, "lg": lambda a, b: a != b, "ne": lambda a, b: a != b, "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
       "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b, "tru": lambda a, b: True, "t": lambda a, b: True, "f": lambda a, b: False, "o": lambda a, b: True, "u": lambda a, b: False,
       "neq": lambda a, b: a != b, "nlt": lambda a, b: not a < b, "nle": lambda a, b: not a <= b, "ngt": lambda a, b: not a > b, "nge": lambda a, b: not a >= b, "nlg": lambda a, b: a == b}
