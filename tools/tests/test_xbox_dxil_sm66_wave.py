import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
SCRIPT = TOOLS / "xbox" / "platform" / "dxil_sm66_wave.py"


def module(declarations):
    lines = [
        'source_filename = "test"',
        'target triple = "dxil-ms-dx"',
        '%dx.types.Handle = type { i8* }',
        '',
        '!1 = !{i32 1, i32 5}',
        '!2 = !{!"cs", i32 6, i32 5}',
        '!3 = !{i32 0, i64 0, i32 4, !2}',
        '',
    ]
    lines.extend(declarations)
    lines.extend([
        '',
        'define void @main() {',
        '  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 0, i32 0, i1 false)',
        '  ret void',
        '}',
        '',
        'attributes #0 = { nounwind readnone }',
        '',
    ])
    return "\n".join(lines)


class XboxDxilSm66WaveDeclarationTests(unittest.TestCase):
    def rewritten(self, declarations):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.ll"
            target = Path(directory) / "out.ll"
            source.write_text(module(declarations), encoding="utf-8")
            subprocess.run([sys.executable, str(SCRIPT), str(source), str(target), "64"],
                           check=True, capture_output=True, text=True)
            return target.read_text(encoding="utf-8")

    def assert_declarations(self, result):
        for name in ("createHandleFromBinding", "annotateHandle"):
            self.assertIn("declare %dx.types.Handle @dx.op." + name + "(", result)
            self.assertRegex(result, r"call [^@\n]*@dx\.op\." + name + r"\(")
        self.assertLess(result.index("declare %dx.types.Handle @dx.op.createHandleFromBinding("),
                        result.index("define void @main()"))

    def test_surviving_declaration_is_kept(self):
        result = self.rewritten([
            'declare i32 @keep(i32) #0',
            'declare %dx.types.Handle @dx.op.createHandle(i32, i8, i32, i32, i1) #0',
        ])
        self.assert_declarations(result)
        self.assertIn("declare i32 @keep(i32) #0", result)

    def test_removed_declaration_was_the_only_one(self):
        result = self.rewritten([
            'declare %dx.types.Handle @dx.op.createHandle(i32, i8, i32, i32, i1) #0',
        ])
        self.assert_declarations(result)

    def test_no_declarations_at_all(self):
        result = self.rewritten([])
        self.assert_declarations(result)


if __name__ == "__main__":
    unittest.main()
