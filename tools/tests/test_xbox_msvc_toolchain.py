import sys
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


if __name__ == "__main__":
    unittest.main()
