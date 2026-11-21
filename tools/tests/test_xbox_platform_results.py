import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
SCRIPT = TOOLS / "xbox" / "platform" / "make_platform_results.py"


@unittest.skipUnless(sys.platform == "win32", "ctypes.windll is Windows only")
class MakePlatformResultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.results = self.directory / "results.json"
        self.results.write_text("{}")

    def run_script(self, imports):
        source = self.directory / "imports.json"
        source.write_text(json.dumps(imports))
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source), str(self.results)],
            capture_output=True,
            text=True,
        )

    def result_platform(self):
        data = json.loads(self.results.read_text())
        self.assertIn("platform", data)
        return data["platform"]

    def test_delay_import_is_classified(self):
        imports = {
            "app.exe": {
                "machine": "0x8664",
                "imports": {"xmem.dll": ["XMemAlloc"]},
                "delay": {"delaypkg.dll": ["DelayFn"]},
                "exports": [],
            },
            "delaypkg.dll": {
                "machine": "0x8664",
                "imports": {},
                "delay": {},
                "exports": ["DelayFn"],
            },
        }
        proc = self.run_script(imports)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        platform = self.result_platform()
        self.assertEqual(platform["xmem.dll!XMemAlloc"], "shim")
        self.assertEqual(platform["delaypkg.dll!DelayFn"], "package")

    def test_missing_dll_is_failed_without_crashing(self):
        imports = {
            "app.exe": {
                "machine": "0x8664",
                "imports": {"definitely_not_a_real_dll_zzz.dll": ["MissingFn"]},
                "delay": {},
                "exports": [],
            }
        }
        proc = self.run_script(imports)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        platform = self.result_platform()
        self.assertEqual(platform["definitely_not_a_real_dll_zzz.dll!MissingFn"], "fail")
        self.assertIn("definitely_not_a_real_dll_zzz.dll!MissingFn", proc.stdout)


if __name__ == "__main__":
    unittest.main()
