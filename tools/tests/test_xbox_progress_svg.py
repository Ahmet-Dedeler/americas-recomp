import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "tools" / "xbox" / "make_progress_svg.py"
COMMITTED = ROOT / "docs" / "xbox" / "progress.svg"


class ProgressSvgTest(unittest.TestCase):
    def test_committed_map_matches_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "progress.svg"
            subprocess.run([sys.executable, str(SCRIPT), str(out)], check=True, capture_output=True)
            self.assertEqual(
                out.read_bytes(),
                COMMITTED.read_bytes(),
                "docs/xbox/progress.svg is stale: run python tools/xbox/make_progress_svg.py",
            )


if __name__ == "__main__":
    unittest.main()
