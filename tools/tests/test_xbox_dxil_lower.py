import importlib.util
import re
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("xbox_dxil_lower", TOOLS / "xbox" / "platform" / "xbox_dxil_lower.py")
lower_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lower_module
spec.loader.exec_module(lower_module)


def module(declarations):
    lines = [
        'source_filename = "test"',
        'target triple = "dxil-ms-xb2"',
        '%dx.types.Handle = type { i8* }',
        '',
    ]
    lines.extend(declarations)
    lines.extend([
        '',
        'define void @main() {',
        '  %0 = call i32 @xbox.Med3_U32(i32 0, i32 1, i32 2, i32 3)',
        '  ret void',
        '}',
        '',
        'attributes #0 = { nounwind readnone }',
        '',
    ])
    return "\n".join(lines)


class XboxDxilLowerDeclarationTests(unittest.TestCase):
    def lowered(self, declarations):
        result, count, left = lower_module.lower(module(declarations))
        self.assertEqual(count, 1)
        self.assertEqual(left, set())
        return result

    def assert_calls_declared(self, result):
        called = set(re.findall(r"call [^@\n]*@(dx\.op\.[\w.]+)\(", result))
        declared = set(re.findall(r"^declare [^\n]*@(dx\.op\.[\w.]+)\(", result, re.M))
        self.assertTrue(called)
        self.assertLessEqual(called, declared, "called DXIL operations must be declared")

    def assert_decls_before_first_define(self, result):
        self.assertLess(result.index("declare i32 @dx.op.binary.i32("), result.index("define void @main()"))

    def test_surviving_declaration_is_kept(self):
        result = self.lowered([
            'declare i32 @keep(i32) #0',
            'declare i32 @xbox.Med3_U32(i32, i32, i32, i32) #0',
        ])
        self.assert_calls_declared(result)
        self.assert_decls_before_first_define(result)
        self.assertIn("declare i32 @keep(i32) #0", result)
        self.assertNotIn("@xbox.", result)

    def test_removed_intrinsic_was_the_only_declaration(self):
        result = self.lowered([
            'declare i32 @xbox.Med3_U32(i32, i32, i32, i32) #0',
        ])
        self.assert_calls_declared(result)
        self.assert_decls_before_first_define(result)

    def test_no_declarations_at_all(self):
        result = self.lowered([])
        self.assert_calls_declared(result)
        self.assert_decls_before_first_define(result)


if __name__ == "__main__":
    unittest.main()
