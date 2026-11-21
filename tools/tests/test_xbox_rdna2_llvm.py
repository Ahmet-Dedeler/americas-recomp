import importlib.util
import os
import shutil
import struct
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
MC = os.environ.get("XBOX_LLVM_MC") or shutil.which("llvm-mc")


@unittest.skipUnless(MC, "llvm-mc not found (set XBOX_LLVM_MC)")
class SmemTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["XBOX_LLVM_MC"] = MC
        spec = importlib.util.spec_from_file_location("rdna2_llvm", TOOLS / "xbox" / "rdna2_llvm.py")
        cls.r = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.r)

    def test_buffer_loads_after_an_invalid_slot(self):
        smem = self.r.SMEM
        self.assertEqual(smem.get(0), "s_load_dword")
        self.assertEqual(smem.get(8), "s_buffer_load_dword")
        self.assertEqual(smem.get(12), "s_buffer_load_dwordx16")

    def test_smem_matches_one_slot_per_run(self):
        alone = {}
        for op in range(64):
            enc = struct.pack("<2I", 0xF4000000 | (op << 18), 0x7D << 25)
            d = self.r._decode(enc)
            if d and d[0][1] == enc:
                alone[op] = self.r._SUFFIX.sub("", d[0][0])
        self.assertEqual({k: v for k, v in self.r.SMEM.items() if k < 64}, alone)


if __name__ == "__main__":
    unittest.main()
