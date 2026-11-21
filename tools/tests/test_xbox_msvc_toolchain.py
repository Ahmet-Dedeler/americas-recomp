import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xbox"))
import build_msvc


class MsvcToolchainTest(unittest.TestCase):
    @patch("build_msvc.subprocess.check_output")
    def test_requires_cpp_tools_across_all_products(self, run):
        run.side_effect = ["C:/Build Tools\n", "Path=compiler-bin\n"]
        self.assertEqual(build_msvc.vc_env()["Path"], "compiler-bin")
        query = run.call_args_list[0].args[0]
        self.assertIn("-latest", query)
        self.assertEqual(query[query.index("-products") + 1], "*")
        self.assertEqual(query[query.index("-requires") + 1],
                         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64")

    @patch("build_msvc.subprocess.check_output", return_value="\r\n")
    def test_no_matching_installation_stops_before_vcvars(self, run):
        with self.assertRaisesRegex(SystemExit, "x64 C\\+\\+ build tools"):
            build_msvc.vc_env()
        run.assert_called_once()


class StaleObjectTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.source, self.header, self.obj, self.deps = root / "a.cpp", root / "a.hpp", root / "a.obj", root / "a.json"
        for path in (self.source, self.header, self.obj):
            path.write_text("x")
        self.deps.write_text(json.dumps({"Version": "1.2", "Data": {"Source": str(self.source), "Includes": [str(self.header)]}}))
        for path in (self.source, self.header):
            os.utime(path, (1000, 1000))
        for path in (self.obj, self.deps):
            os.utime(path, (2000, 2000))

    def tearDown(self):
        self.dir.cleanup()

    def stale(self):
        return build_msvc.stale(self.obj, self.source, self.deps)

    def test_up_to_date(self):
        self.assertFalse(self.stale())

    def test_changed_source(self):
        os.utime(self.source, (3000, 3000))
        self.assertTrue(self.stale())

    def test_changed_header(self):
        os.utime(self.header, (3000, 3000))
        self.assertTrue(self.stale())

    def test_removed_header(self):
        self.header.unlink()
        self.assertTrue(self.stale())

    def test_missing_or_unreadable_dependencies(self):
        self.deps.write_text("not json")
        self.assertTrue(self.stale())
        self.deps.unlink()
        self.assertTrue(self.stale())


if __name__ == "__main__":
    unittest.main()
