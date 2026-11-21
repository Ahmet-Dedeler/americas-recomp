import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "xbox/platform/xgr/idl_table.py"

IDL = """
[
    uuid(11111111-2222-3333-4444-555555555555)
]
interface IXSampleImpl : IUnknown
{
    HRESULT XSampleFirst( [in] UINT32 value );
    HRESULT __PADDING__();
    HRESULT XSampleSecond( [out] UINT32 *value );
    HRESULT __PADDING_2__();
    HRESULT __PADDING_3__();
    void XSampleThird();
}

[
    uuid(66666666-7777-8888-9999-aaaaaaaaaaaa)
]
interface IXSampleImpl2 : IXSampleImpl
{
    HRESULT XSampleFourth();
}
"""


class IdlTableSlots(unittest.TestCase):
    def table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "xsample.idl").write_text(IDL, encoding="utf-8")
            out = root / "table.json"
            run = subprocess.run([sys.executable, "-I", str(SCRIPT), str(root), str(out)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(out.read_text(encoding="utf-8"))["interfaces"], run.stdout

    def test_unnamed_slots_advance_the_methods_after_them(self):
        interfaces, _ = self.table()
        slots = {m["name"]: m["slot"] for i in interfaces for m in i["methods"] if not m["unnamed"]}
        self.assertEqual(slots, {"XSampleFirst": 3, "XSampleSecond": 5, "XSampleThird": 8, "XSampleFourth": 9})

    def test_unnamed_slots_are_kept_and_not_counted_as_methods(self):
        interfaces, printed = self.table()
        base = next(i for i in interfaces if i["name"] == "IXSampleImpl")
        self.assertEqual([m["slot"] for m in base["methods"] if m["unnamed"]], [4, 6, 7])
        self.assertEqual(base["nslots"], 9)
        self.assertIn("2 interfaces, 4 methods", printed)


if __name__ == "__main__":
    unittest.main()
