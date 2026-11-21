import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "xbox"))
os.environ.setdefault("XBOX_RDNA2", "recomp")
import rdna_asm

OP_CAPABILITY = 17
CAP_MIN_LOD = 42
OP_IMAGE_SAMPLE_EXPLICIT_LOD = 88
IMAGE_OPERANDS_GRAD_AND_MIN_LOD = 0x04 | 0x80


def _find_driver():
    env = os.environ.get("XBOX_DRIVER")
    if env and Path(env).is_file():
        return env
    for name in ("driver", "driver.exe"):
        candidate = ROOT / "build-xbox" / name
        if candidate.is_file():
            return str(candidate)
    return None


def _find_spirv_val():
    env = os.environ.get("XBOX_SPIRV_VAL")
    if env and Path(env).is_file():
        return env
    return shutil.which("spirv-val")


def _parse_spirv(words):
    cursor = 5
    while cursor < len(words):
        count = words[cursor] >> 16
        op = words[cursor] & 0xFFFF
        yield op, words[cursor + 1 : cursor + count]
        cursor += count


def _sample_grad_clamp_compute():
    p = rdna_asm.Program()
    p.smem("s_load_dwordx8", 8, 4, 0)
    p.smem("s_load_dwordx4", 16, 4, 32)
    p.smem("s_load_dwordx4", 20, 4, 48)
    p.sopp("s_waitcnt", 0)
    for r in (1, 2, 3, 4):
        p.vop1("v_mov_b32", r, 0)
    p.vop1("v_mov_b32", 5, ("lit", 0x3F000000))
    p.vop1("v_mov_b32", 6, ("lit", 0x3F000000))
    p.vop1("v_mov_b32", 7, ("lit", 0x3F800000))
    p.mimg("image_sample_d_cl", 9, 1, 8, dmask=1, ssamp=16)
    p.sopp("s_waitcnt", 0)
    p.vop2("v_lshlrev_b32", 8, 2, "v0")
    p.mubuf("buffer_store_dword", 9, 8, 20, 128, 0)
    p.sopp("s_endpgm")
    return p.bytes()


def _sample_grad_clamp_pixel():
    p = rdna_asm.Program()
    p.smem("s_load_dwordx8", 8, 4, 0)
    p.smem("s_load_dwordx4", 16, 4, 32)
    p.sopp("s_waitcnt", 0)
    for r in (2, 3, 4, 5):
        p.vop1("v_mov_b32", r, 0)
    p.vop1("v_mov_b32", 6, ("lit", 0x3F000000))
    p.vop1("v_mov_b32", 7, ("lit", 0x3F000000))
    p.vop1("v_mov_b32", 8, ("lit", 0x3F800000))
    p.mimg("image_sample_d_cl", 9, 2, 8, dmask=0xF, ssamp=16)
    p.sopp("s_waitcnt", 0)
    exp_mrt0_done = 0xF800080F
    p.words += [exp_mrt0_done, (12 << 24) | (11 << 16) | (10 << 8) | 9]
    p.sopp("s_endpgm")
    return p.bytes()


class MinLodCapabilityTest(unittest.TestCase):
    def test_driver_and_exec_wire_min_lod_capability(self):
        driver_src = (ROOT / "tools" / "xbox" / "driver.cpp").read_text(encoding="utf-8")
        exec_src = (ROOT / "tools" / "xbox" / "exec.cpp").read_text(encoding="utf-8")
        self.assertIn("41u,42u,", driver_src)
        self.assertIn("shaderResourceMinLod", exec_src)

    def _compile_and_check(self, code_bytes, stage, bind_text):
        driver = _find_driver()
        if not driver:
            self.skipTest("driver binary not built (set XBOX_DRIVER or build tools/xbox)")
        with tempfile.TemporaryDirectory(prefix="minlod_") as tmp:
            code_path = Path(tmp) / "shader.bin"
            bind_path = Path(tmp) / "shader.bind"
            spv_path = Path(tmp) / "out.spv"
            code_path.write_bytes(code_bytes)
            bind_path.write_text(bind_text, encoding="utf-8")
            cmd = [driver, str(code_path), "32", str(spv_path), str(bind_path), "1", stage]
            if stage == "ps":
                cmd += ["2", "0", "0"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            raw = spv_path.read_bytes()
            words = struct.unpack("<%dI" % (len(raw) // 4), raw)
            insts = list(_parse_spirv(words))
            caps = {ops[0] for op, ops in insts if op == OP_CAPABILITY and ops}
            self.assertIn(CAP_MIN_LOD, caps)
            samples = [ops for op, ops in insts if op == OP_IMAGE_SAMPLE_EXPLICIT_LOD]
            self.assertTrue(
                any(len(ops) >= 5 and (ops[4] & IMAGE_OPERANDS_GRAD_AND_MIN_LOD) == IMAGE_OPERANDS_GRAD_AND_MIN_LOD for ops in samples),
                "expected OpImageSampleExplicitLod with Grad | MinLod",
            )
            val = _find_spirv_val()
            if val:
                vp = subprocess.run([val, "--target-env", "vulkan1.3", str(spv_path)], capture_output=True, text=True, timeout=30)
                self.assertEqual(vp.returncode, 0, vp.stdout + vp.stderr)

    def test_image_sample_d_cl_compute(self):
        bind = "node 0 user 4\ndesc 0 0 img1\ndesc 0 32 samp\ndesc 0 48 buf\n"
        self._compile_and_check(_sample_grad_clamp_compute(), "cs", bind)

    def test_image_sample_d_cl_pixel(self):
        bind = "node 0 user 4\ndesc 0 0 img1\ndesc 0 32 samp\n"
        self._compile_and_check(_sample_grad_clamp_pixel(), "ps", bind)


if __name__ == "__main__":
    unittest.main()
