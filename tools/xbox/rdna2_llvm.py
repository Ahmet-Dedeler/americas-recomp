"""Opcode tables and a disassembler for rdna_asm.py, taken from LLVM's AMDGPU disassembler (llvm-mc, gfx1030) instead of rdna2.py.
Each table maps opcode number -> mnemonic, found by decoding every opcode slot of the format. Set XBOX_LLVM_MC to pick the llvm-mc binary."""
import os, re, shutil, struct, subprocess

MC = os.environ.get("XBOX_LLVM_MC") or shutil.which("llvm-mc") or "llvm-mc"
CPU = os.environ.get("XBOX_LLVM_CPU", "gfx1030")
_LINE = re.compile(r"^\s*([a-z0-9_]+)\b[^;]*;\s*encoding:\s*\[([^\]]*)\]")
_SUFFIX = re.compile(r"_e(32|64)$")


def _decode(blob):
    text = " ".join("0x%02x" % b for b in blob)
    p = subprocess.run([MC, "--disassemble", "--show-encoding", "-triple=amdgcn--amdpal", "-mcpu=" + CPU],
                       input=text, capture_output=True, text=True)
    out = []
    for line in p.stdout.splitlines():
        m = _LINE.match(line)
        if m:
            enc = bytes(int(x, 16) for x in m.group(2).split(",") if x.strip())
            out.append((m.group(1), enc, line.split(";")[0].strip()))
    return out


def _table(count, words):
    blob, want = b"", {}
    for op in range(count):
        enc = struct.pack("<%dI" % len(words(op)), *words(op))
        want[enc] = op
        blob += enc
    table = {}
    for name, enc, _ in _decode(blob):
        op = want.get(enc)
        if op is not None and op not in table:
            table[op] = _SUFFIX.sub("", name)
    return table


V0 = 256
SOPP = _table(128, lambda op: [0xBF800000 | (op << 16)])
SOP1 = _table(256, lambda op: [0xBE800000 | (op << 8)])
SOP2 = _table(128, lambda op: [0x80000000 | (op << 23)])
SOPC = _table(128, lambda op: [0xBF000000 | (op << 16)])
SMEM = _table(256, lambda op: [0xF4000000 | (op << 18), 0x7D << 25])
VOP1 = _table(128, lambda op: [0x7E000000 | (op << 9) | V0])
VOP2 = _table(64, lambda op: [(op << 25) | V0])
VOPC = _table(256, lambda op: [0x7C000000 | (op << 17) | V0])
MUBUF = _table(128, lambda op: [0xE0000000 | (op << 18), 0])
MTBUF = _table(16, lambda op: [0xE8000000 | (1 << 19) | ((op & 7) << 16), ((op >> 3) & 1) << 21])
DS = _table(256, lambda op: [0xD8000000 | (op << 18), 0])
MIMG = {**_table(256, lambda op: [0xF0000000 | ((op & 0x7f) << 18) | ((op >> 7) & 1) | (1 << 15) | (1 << 12) | (0xf << 8), 4]),
        **_table(256, lambda op: [0xF0000000 | ((op & 0x7f) << 18) | ((op >> 7) & 1) | (1 << 3) | (0xf << 8), 0])}


class _Ins:
    def __init__(self, text):
        self.text = text


def sweep(blob, start, end):
    return [_Ins(t) for _, _, t in _decode(blob[start:end])], end
