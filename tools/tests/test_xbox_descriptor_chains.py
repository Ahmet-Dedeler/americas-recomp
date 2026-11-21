import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "xbox"))
os.environ.setdefault("XBOX_RDNA2", "recomp")
import rdna_asm


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


def _phi_multiple_offset_shader():
    p = rdna_asm.Program()
    p.smem("s_load_dwordx4", 8, 4, 0)
    p.smem("s_load_dwordx2", 12, 4, 16)
    p.smem("s_load_dwordx4", 24, 4, 32)
    p.smem("s_load_dword", 28, 4, 48)
    p.sopp("s_waitcnt", 0)
    p.vop2("v_and_b32", 6, 1, "v0")
    p.vop1("v_readfirstlane_b32", 31, "v6")
    p.sop2("s_lshl_b32", 31, "s31", 2)
    p.smem("s_buffer_load_dword", 29, 8, 0, soffset="s31")
    p.smem("s_buffer_load_dword", 30, 8, 4, soffset="s31")
    p.sopp("s_waitcnt", 0)
    p.sopc("s_cmp_eq_u32", "s28", 0)
    to_a = p.branch_fwd("s_cbranch_scc1")
    p.sop2("s_lshl_b32", 20, "s30", 5)
    to_join = p.branch_fwd("s_branch")
    p.patch_to_here(to_a)
    p.sop2("s_lshl_b32", 20, "s29", 5)
    p.patch_to_here(to_join)
    p.smem("s_load_dwordx8", 16, 12, 0, soffset="s20")
    p.sopp("s_waitcnt", 0)
    p.vop1("v_mov_b32", 1, "v0")
    p.vop1("v_mov_b32", 2, 0)
    p.mimg("image_load", 4, 1, 16, dmask=1)
    p.sopp("s_waitcnt", 0)
    p.vop2("v_lshlrev_b32", 5, 2, "v0")
    p.mubuf("buffer_store_dword", 4, 5, 24, 128, 0)
    p.sopp("s_endpgm")
    return p.bytes()


def _phi_same_heap_loads_shader():
    p = rdna_asm.Program()
    p.smem("s_load_dwordx4", 8, 4, 0)
    p.smem("s_load_dwordx2", 12, 4, 16)
    p.smem("s_load_dwordx4", 24, 4, 32)
    p.smem("s_load_dword", 28, 4, 48)
    p.sopp("s_waitcnt", 0)
    p.smem("s_buffer_load_dword", 29, 8, 0)
    p.smem("s_buffer_load_dword", 30, 8, 4)
    p.sopp("s_waitcnt", 0)
    p.sopc("s_cmp_eq_u32", "s28", 0)
    to_a = p.branch_fwd("s_cbranch_scc1")
    p.sop2("s_lshl_b32", 20, "s30", 5)
    p.smem("s_load_dwordx8", 16, 12, 0, soffset="s20")
    to_join = p.branch_fwd("s_branch")
    p.patch_to_here(to_a)
    p.sop2("s_lshl_b32", 20, "s29", 5)
    p.smem("s_load_dwordx8", 16, 12, 0, soffset="s20")
    p.patch_to_here(to_join)
    p.sopp("s_waitcnt", 0)
    p.vop1("v_mov_b32", 1, "v0")
    p.vop1("v_mov_b32", 2, 0)
    p.mimg("image_load", 4, 1, 16, dmask=1)
    p.sopp("s_waitcnt", 0)
    p.vop2("v_lshlrev_b32", 5, 2, "v0")
    p.mubuf("buffer_store_dword", 4, 5, 24, 128, 0)
    p.sopp("s_endpgm")
    return p.bytes()


def _table_image_with_sampler_phi_shader():
    p = rdna_asm.Program()
    p.smem("s_load_dword", 2, 4, 0)
    p.smem("s_load_dwordx2", 6, 4, 8)
    p.smem("s_load_dwordx4", 24, 4, 16)
    p.smem("s_load_dwordx4", 28, 4, 32)
    p.sopp("s_waitcnt", 0)
    p.sopc("s_cmp_eq_u32", "s2", 0)
    to_a = p.branch_fwd("s_cbranch_scc1")
    p.smem("s_load_dwordx4", 12, 4, 64)
    p.vop2("v_lshlrev_b32", 5, 2, "v0")
    p.mubuf("buffer_load_dword", 3, 5, 28, 128, 0)
    to_join = p.branch_fwd("s_branch")
    p.patch_to_here(to_a)
    p.smem("s_load_dwordx4", 12, 4, 48)
    p.vop2("v_lshlrev_b32", 5, 2, "v0")
    p.mubuf("buffer_load_dword", 3, 5, 28, 128, 0)
    p.patch_to_here(to_join)
    p.sopp("s_waitcnt", 0)
    p.vop1("v_mov_b32", 1, ("lit", 0x3FA00000))
    p.vop1("v_mov_b32", 2, ("lit", 0x3F000000))
    top = p.label()
    p.vop1("v_readfirstlane_b32", 0, "v3")
    p.vopc("v_cmp_eq_u32", "s0", "v3")
    p.sop1("s_and_saveexec_b32", 1, rdna_asm.VCC_LO)
    p.sop2("s_lshl_b32", 0, "s0", 5)
    p.smem("s_load_dwordx8", 16, 6, 0, soffset="s0")
    p.sopp("s_waitcnt", 0)
    p.mimg("image_sample_lz", 4, 1, 16, dmask=1, ssamp=12)
    p.sopp("s_waitcnt", 0)
    p.mubuf("buffer_store_dword", 4, 5, 24, 128, 0)
    p.sop2("s_xor_b32", rdna_asm.EXEC_LO, rdna_asm.EXEC_LO, "s1")
    p.branch_to("s_cbranch_execnz", top)
    p.sopp("s_endpgm")
    return p.bytes()


def _deep_foldable_sampler_web_shader():
    p = rdna_asm.Program()
    p.smem("s_load_dwordx8", 16, 4, 0)
    p.smem("s_load_dwordx4", 24, 4, 32)
    p.smem("s_load_dwordx4", 12, 4, 48)
    p.smem("s_load_dword", 2, 4, 64)
    p.sopp("s_waitcnt", 0)
    p.sopc("s_cmp_eq_u32", "s2", 0)
    to_valid = p.branch_fwd("s_cbranch_scc1")
    p.sop1("s_mov_b32", 12, rdna_asm.EXEC_LO)
    for group, reg in enumerate((13, 14, 15)):
        for k in range(9):
            p.sopc("s_cmp_eq_u32", "s2", group * 9 + k + 1)
            skip = p.branch_fwd("s_cbranch_scc0")
            p.sop1("s_mov_b32", reg, k + 1)
            p.patch_to_here(skip)
    p.patch_to_here(to_valid)
    p.vop1("v_mov_b32", 1, ("lit", 0x3F000000))
    p.vop1("v_mov_b32", 2, ("lit", 0x3F000000))
    p.mimg("image_sample_lz", 4, 1, 16, dmask=1, ssamp=12)
    p.sopp("s_waitcnt", 0)
    p.vop2("v_lshlrev_b32", 5, 2, "v0")
    p.mubuf("buffer_store_dword", 4, 5, 24, 128, 0)
    p.sopp("s_endpgm")
    return p.bytes()


class DescriptorChainRecompileTest(unittest.TestCase):
    def _compile_and_validate(self, code_bytes, bind_text):
        driver = _find_driver()
        if not driver:
            self.skipTest("driver binary not built (set XBOX_DRIVER or build tools/xbox)")
        with tempfile.TemporaryDirectory(prefix="desc_chain_") as tmp:
            code_path = Path(tmp) / "shader.bin"
            bind_path = Path(tmp) / "shader.bind"
            spv_path = Path(tmp) / "out.spv"
            code_path.write_bytes(code_bytes)
            bind_path.write_text(bind_text, encoding="utf-8")
            proc = subprocess.run([driver, str(code_path), "32", str(spv_path), str(bind_path), "1", "cs"], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            val = _find_spirv_val()
            if val:
                vp = subprocess.run([val, "--target-env", "vulkan1.3", str(spv_path)], capture_output=True, text=True, timeout=30)
                self.assertEqual(vp.returncode, 0, vp.stdout + vp.stderr)

    def test_phi_multiple_offset_image_table(self):
        bind = "node 0 user 4\nnode 1 child 0 16\ndesc 0 0 buf\ndesc 0 32 buf\ndesc 1 0 img1\n"
        self._compile_and_validate(_phi_multiple_offset_shader(), bind)

    def test_phi_same_heap_image_loads(self):
        bind = "node 0 user 4\nnode 1 child 0 16\ndesc 0 0 buf\ndesc 0 32 buf\ndesc 1 0 img1\n"
        self._compile_and_validate(_phi_same_heap_loads_shader(), bind)

    def test_table_image_with_sampler_phi(self):
        bind = "node 0 user 4\nnode 1 child 0 8\ndesc 0 16 buf\ndesc 0 32 buf\ndesc 0 48 samp\ndesc 0 64 samp\ndesc 1 0 img1\n"
        self._compile_and_validate(_table_image_with_sampler_phi_shader(), bind)

    def test_deep_foldable_sampler_web_short_circuits(self):
        bind = "node 0 user 4\ndesc 0 0 img1\ndesc 0 32 buf\ndesc 0 48 samp\ndesc 0 80 samp\n"
        self._compile_and_validate(_deep_foldable_sampler_web_shader(), bind)


if __name__ == "__main__":
    unittest.main()
