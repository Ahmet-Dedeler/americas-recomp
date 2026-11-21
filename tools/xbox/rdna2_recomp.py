"""RDNA2 (gfx10.3) instruction splitter with the interface of out_disasm's rdna2.py, for machines that do not have that file: cuts a code
blob into instructions, names them and keeps their raw words. It does not decode operands; cpu_ref.py and bindings.py read the fields
from the raw words. It needs nothing outside this repository (rdna2_llvm.py needs llvm-mc and gives the assembler's tables only).

Opcode numbers come from the shader recompiler's own decoder tables (core/shader/recompiler/RdnaDecoder/src), read when this module
is imported, and instruction lengths follow the same rules, so the tools and the recompiler agree on where instructions start.
Names are the recompiler's opcode identifiers in snake case (VCmpxEqU32 -> v_cmpx_eq_u32). Where an identifier names what an
opcode does rather than the instruction (VOP2 0x2c v_fmamk_f32 is VMadmkF32, VOP3 0x30f v_add_co_u32 is VAddI32), MNEMONICS gives the
ISA name (AMD "RDNA 2" Instruction Set Architecture reference guide; checked against the gfx10 tables of LLVM's AMDGPU back end).
An opcode the recompiler does not decode is named "<format>_op_0x<opcode>".

Interface (as rdna2.py):
  decode(code, off) -> Inst                one instruction at byte offset off of a bytes-like blob (never raises for bad data)
  sweep(code, start, end) -> (insts, bad)  linear split of [start, end); bad lists the byte offsets of words that are not an
                                           instruction (unknown encoding family, or longer than the range); each is skipped as one word
  SOP1 SOP2 SOPK SOPC SOPP SMEM VOP1 VOP2 VOPC VOP3 VOP3P VINTRP DS MUBUF MTBUF FLAT MIMG   {opcode: name}
  Inst: off, size (bytes), words (u32 list), fmt, name, text ("<name> <raw words>")
fmt is the encoding: VOP1/VOP2/VOPC carrying a DPP, DPP8 or SDWA word are "VOP2+DPP" / "VOP2+DPP8" / "VOP2+SDWA", MIMG with NSA
address words is "MIMG+NSA<n>", the FLAT encoding is FLAT, SCRATCH or GLOBAL by segment, an undecodable word is "UNKNOWN".
"""
import pathlib, re, struct

SRC = pathlib.Path(__file__).resolve().parents[2] / "core/shader/recompiler/RdnaDecoder/src"

MNEMONICS = {
    ("SOPK", 0x03): "s_cmpk_eq_i32", ("SOPK", 0x04): "s_cmpk_lg_i32", ("SOPK", 0x05): "s_cmpk_gt_i32", ("SOPK", 0x06): "s_cmpk_ge_i32",
    ("SOPK", 0x07): "s_cmpk_lt_i32", ("SOPK", 0x08): "s_cmpk_le_i32", ("SOPK", 0x09): "s_cmpk_eq_u32", ("SOPK", 0x0a): "s_cmpk_lg_u32",
    ("SOPK", 0x0b): "s_cmpk_gt_u32", ("SOPK", 0x0c): "s_cmpk_ge_u32", ("SOPK", 0x0d): "s_cmpk_lt_u32", ("SOPK", 0x0e): "s_cmpk_le_u32",
    ("SOPK", 0x0f): "s_addk_i32",
    ("SOPK", 0x17): "s_waitcnt_vscnt", ("SOPK", 0x18): "s_waitcnt_vmcnt", ("SOPK", 0x19): "s_waitcnt_expcnt", ("SOPK", 0x1a): "s_waitcnt_lgkmcnt",
    ("SOPP", 0x0b): "s_setkill",
    ("SOPP", 0x17): "s_cbranch_cdbgsys", ("SOPP", 0x18): "s_cbranch_cdbguser", ("SOPP", 0x19): "s_cbranch_cdbgsys_or_user",
    ("SOPP", 0x1a): "s_cbranch_cdbgsys_and_user", ("SOPP", 0x1b): "s_endpgm_saved", ("SOPP", 0x1e): "s_endpgm_ordered_ps_done",
    ("SOPP", 0x28): "s_ttracedata_imm",
    ("SOP1", 0x49): "s_movrelsd_2_b32", ("VOP1", 0x48): "v_movrelsd_2_b32",
    ("VOP2", 0x06): "v_fmac_legacy_f32",
    ("VOP2", 0x28): "v_add_co_ci_u32", ("VOP2", 0x2b): "v_fmac_f32", ("VOP2", 0x2c): "v_fmamk_f32", ("VOP2", 0x2d): "v_fmaak_f32",
    ("VOP3", 0x140): "v_fma_legacy_f32", ("VOP3", 0x30f): "v_add_co_u32", ("VOP3", 0x310): "v_sub_co_u32", ("VOP3", 0x319): "v_subrev_co_u32",
    ("VOP3P", 0x20): "v_fma_mix_f32", ("VOP3P", 0x21): "v_fma_mixlo_f16", ("VOP3P", 0x22): "v_fma_mixhi_f16",
}

VOP2_LITERAL = {"VMadmkF32", "VMadakF32", "VFmamkF16", "VFmaakF16"}
EXTRA_WORD = {249: "SDWA", 250: "DPP", 233: "DPP8", 234: "DPP8"}


def _snake(ident):
    return "_".join(t.lower() for t in re.findall(r"[A-Z][a-z0-9]*", ident))


def _array(text, table):
    body = text[text.index(table + "[] = {"):]
    body = body[:body.index("};")]
    return [(int(c, 16), op, label) for c, op, label in
            re.findall(r'\{0x([0-9a-fA-F]+)u,\s*RdnaOpcode::(\w+)(?:,\s*"([^"]*)")?', body)]


def _switch(text, function):
    body = text[text.index("RdnaOpcode " + function + "("):]
    body = body[:body.index("\n}\n")]
    rows, pending = [], []
    for case, op in re.findall(r"case 0x([0-9a-fA-F]+)u:|return RdnaOpcode::(\w+);", body):
        if case:
            pending.append(int(case, 16))
        elif pending:
            rows += [(c, op, "") for c in pending]
            pending = []
    return rows


def _table(fmt, rows):
    return {code: (MNEMONICS.get((fmt, code)) or label or _snake(op), op) for code, op, label in rows}


def _idents(text, signature):
    body = text[text.index(signature):]
    return set(re.findall(r"RdnaOpcode::(\w+)", body[:body.index("\n}\n")]))


def _load():
    scalar = (SRC / "RdnaScalarOpDecoder.cpp").read_text(encoding="utf-8")
    vector = (SRC / "RdnaVectorOpDecoder.cpp").read_text(encoding="utf-8")
    memory = (SRC / "RdnaMemoryOpDecoder.cpp").read_text(encoding="utf-8")
    image = (SRC / "RdnaImageOpDecoder.cpp").read_text(encoding="utf-8")
    t = {
        "SOP1": _table("SOP1", _switch(scalar, "decodeSop1Opcode")), "SOP2": _table("SOP2", _switch(scalar, "decodeSop2Opcode")),
        "SOPC": _table("SOPC", _switch(scalar, "decodeSopcOpcode")), "SOPK": _table("SOPK", _switch(scalar, "decodeSopkOpcode")),
        "SOPP": _table("SOPP", _switch(scalar, "decodeSoppOpcode")), "VOP1": _table("VOP1", _array(vector, "vop1Opcodes")),
        "VOP2": _table("VOP2", _array(vector, "vop2Opcodes")), "VOPC": _table("VOPC", _array(vector, "vopcOpcodes")),
        "VOP3P": _table("VOP3P", _array(vector, "vop3pOpcodes")), "VINTRP": _table("VINTRP", _switch(vector, "lookupVintrpOpcode")),
        "SMEM": _table("SMEM", _array(memory, "smemOpcodes")), "MUBUF": _table("MUBUF", _array(memory, "mubufOpcodes")),
        "MTBUF": _table("MTBUF", _array(memory, "mtbufOpcodes")), "FLAT": _table("FLAT", _array(memory, "flatOpcodes")),
        "DS": _table("DS", _array(memory, "dsOpcodes")), "MIMG": _table("MIMG", _array(image, "imageOpcodes")),
    }
    vop3 = {code: entry for code, entry in t["VOPC"].items()}
    vop3.update({0x100 + code: entry for code, entry in t["VOP2"].items() if entry[1] not in VOP2_LITERAL and code not in (0x02, 0x0d, 0x3c)})
    vop3.update({0x180 + code: entry for code, entry in _table("VOP1", _array(vector, "vop3EncodedVop1Opcodes")).items()})
    vop3.update({code: (MNEMONICS.get(("VOP3", code)) or label or _snake(op), op) for code, op, label in _array(vector, "vop3Opcodes")})
    t["VOP3"] = vop3
    no_sources = _idents(vector, "bool isVop1WithoutOperands(") | _idents(vector, "bool isVop3F16InterpolationOpcode(") | \
        _idents(vector, "bool isVop3F32InterpolationOpcode(") | {"SGetpcB64"}
    two_sources = _idents(vector, "std::uint32_t nativeVop3SourceCount(") | _idents(vector, "std::uint32_t vop3pSourceCount(")
    return t, no_sources, two_sources


_T, _NO_SOURCES, _TWO_SOURCES = _load()
SOP1, SOP2, SOPK, SOPC, SOPP = ({k: v[0] for k, v in _T[f].items()} for f in ("SOP1", "SOP2", "SOPK", "SOPC", "SOPP"))
SMEM, VOP1, VOP2, VOPC, VOP3, VOP3P, VINTRP = ({k: v[0] for k, v in _T[f].items()} for f in ("SMEM", "VOP1", "VOP2", "VOPC", "VOP3", "VOP3P", "VINTRP"))
DS, MUBUF, MTBUF, FLAT, MIMG = ({k: v[0] for k, v in _T[f].items()} for f in ("DS", "MUBUF", "MTBUF", "FLAT", "MIMG"))


class Inst:
    def __init__(self, off, words, fmt, name):
        self.off, self.words, self.fmt, self.name = off, words, fmt, name
        self.size = 4 * max(1, len(words))

    @property
    def text(self):
        return self.name + " " + " ".join("%08x" % w for w in self.words)

    def __repr__(self):
        return "<%s @0x%x %s>" % (self.fmt, self.off, self.text)


def _family(w):
    if not w & 0x80000000:
        return {0x3e: "VOPC", 0x3f: "VOP1"}.get((w >> 25) & 0x3f, "VOP2")
    if (w & 0xc0000000) == 0x80000000:
        op = (w >> 23) & 0x7f
        return {0x7d: "SOP1", 0x7e: "SOPC", 0x7f: "SOPP"}.get(op, "SOPK" if op >= 0x60 else "SOP2")
    return {0x32: "VINTRP", 0x33: "VOP3P", 0x35: "VOP3", 0x36: "DS", 0x37: "FLAT", 0x38: "MUBUF", 0x3a: "MTBUF",
            0x3c: "MIMG", 0x3d: "SMEM", 0x3e: "EXP"}.get(w >> 26, "UNKNOWN")


def _shape(fmt, w0, w1):
    """(opcode, word count without a literal, fmt suffix, source fields that may hold the literal code 255)"""
    if fmt == "SOP1":
        return (w0 >> 8) & 0xff, 1, "", (w0 & 0xff,)
    if fmt == "SOP2":
        return (w0 >> 23) & 0x7f, 1, "", (w0 & 0xff, (w0 >> 8) & 0xff)
    if fmt == "SOPC":
        return (w0 >> 16) & 0x7f, 1, "", (w0 & 0xff, (w0 >> 8) & 0xff)
    if fmt == "SOPK":
        return (w0 >> 23) & 0x1f, 1, "", ()
    if fmt == "SOPP":
        return (w0 >> 16) & 0x7f, 1, "", ()
    if fmt in ("VOP1", "VOP2", "VOPC"):
        op = {"VOP1": (w0 >> 9) & 0xff, "VOP2": (w0 >> 25) & 0x3f, "VOPC": (w0 >> 17) & 0xff}[fmt]
        extra = EXTRA_WORD.get(w0 & 0x1ff)
        return (op, 2, "+" + extra, ()) if extra else (op, 1, "", (w0 & 0x1ff,))
    if fmt in ("VOP3", "VOP3P"):
        op = (w0 >> 16) & (0x3ff if fmt == "VOP3" else 0x7f)
        return op, 2, "", (w1 & 0x1ff, (w1 >> 9) & 0x1ff, (w1 >> 18) & 0x1ff)
    if fmt == "VINTRP":
        return (w0 >> 16) & 0x3, 1, "", ()
    if fmt == "MIMG":
        nsa = (w0 >> 1) & 3
        return ((w0 >> 18) & 0x7f) | ((w0 & 1) << 7), 2 + nsa, "+NSA%d" % nsa if nsa else "", ()
    if fmt == "SMEM":
        return (w0 >> 18) & 0xff, 2, "", ()
    if fmt == "MUBUF":
        return ((w0 >> 18) & 0x7f) | (((w0 >> 25) & 1) << 7), 2, "", ()
    if fmt == "MTBUF":
        return ((w0 >> 16) & 0x7) | (((w1 >> 21) & 1) << 3), 2, "", ()
    if fmt == "FLAT":
        return (w0 >> 18) & 0x7f, 2, "", ()
    if fmt == "DS":
        return (w0 >> 18) & 0xff, 2, "", ()
    return 0, 2, "", ()


def _used_sources(fmt, op, ident, srcs):
    """the source fields the recompiler decodes for this opcode (only those can make it read a literal word)"""
    if ident in _NO_SOURCES:
        return ()
    if fmt == "VOP3":
        if op <= 0xff:
            return srcs[:2]
        if 0x180 <= op <= 0x1ff:
            return srcs[:1]
        if 0x100 <= op <= 0x13f:
            return srcs if ident == "VCndmaskB32" or op in (0x128, 0x129, 0x12a) else srcs[:2]
    if fmt in ("VOP3", "VOP3P") and ident in _TWO_SOURCES:
        return srcs[:2]
    return srcs


def decode(code, off, end=None):
    end = len(code) if end is None else min(end, len(code))
    if off + 4 > end:
        return Inst(off, [], "UNKNOWN", "unknown")
    w0 = struct.unpack_from("<I", code, off)[0]
    fmt = _family(w0)
    if fmt == "UNKNOWN":
        return Inst(off, [w0], "UNKNOWN", "unknown")
    w1 = struct.unpack_from("<I", code, off + 4)[0] if off + 8 <= end else 0
    op, count, suffix, srcs = _shape(fmt, w0, w1)
    if fmt == "EXP":
        name = "exp"
    else:
        entry = _T[fmt].get(op)
        name = entry[0] if entry else "%s_op_0x%x" % (fmt.lower(), op)
        srcs = _used_sources(fmt, op, entry[1] if entry else None, srcs)
        if fmt == "VOP1" and entry and entry[1] in _NO_SOURCES:
            count, suffix = 1, ""
        if fmt == "FLAT":
            seg = (w0 >> 14) & 3
            prefix = {1: "scratch_", 2: "global_"}.get(seg)
            if prefix and name.startswith(("flat_", "global_")):
                name = prefix + name.split("_", 1)[1]
            fmt = {1: "SCRATCH", 2: "GLOBAL"}.get(seg, "FLAT")
        if 255 in srcs or (fmt == "SOPK" and entry and entry[1] == "SSetregImm32B32") or (fmt == "VOP2" and not suffix and entry and entry[1] in VOP2_LITERAL):
            count += 1
    if off + 4 * count > end:
        return Inst(off, [w0], "UNKNOWN", "unknown")
    return Inst(off, list(struct.unpack_from("<%dI" % count, code, off)), fmt + suffix, name)


def sweep(code, start, end):
    insts, bad = [], []
    off = start
    while off + 4 <= end:
        inst = decode(code, off, end)
        if inst.fmt == "UNKNOWN":
            bad.append(off)
            off += 4
            continue
        insts.append(inst)
        off += inst.size
    return insts, bad

