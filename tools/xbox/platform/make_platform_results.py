"""Classify every function imported by the package DLLs and store the result under "platform" in docs/xbox/results.json.
usage: make_platform_results.py <imports.json from pe_imports.py> [results.json]
  standard  the import resolves on this Windows PC (LoadLibrary + GetProcAddress)
  package   the DLL is shipped in the package itself and exports the name
  shim      the DLL does not exist on a PC (xmem.dll, d3d12_xs.dll): a stand-in in this directory provides it
Windows only (it loads the system DLLs)."""
import collections, ctypes, json, sys
imports = json.load(open(sys.argv[1]))
path = sys.argv[2] if len(sys.argv) > 2 else "docs/xbox/results.json"
package = {k.lower(): set(v["exports"]) for k, v in imports.items()}
SHIMS = {"xmem.dll", "d3d12_xs.dll"}
gp = ctypes.windll.kernel32.GetProcAddress
gp.restype = ctypes.c_void_p
gp.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
union = collections.defaultdict(set)
for v in imports.values():
    for source in ("imports", "delay"):
        for dll, fns in v.get(source, {}).items():
            union[dll].update(fns)
out, unresolved = {}, []
for dll, fns in sorted(union.items()):
    for fn in sorted(fns):
        key = dll + "!" + fn
        if dll in SHIMS:
            out[key] = "shim"
        elif dll in package:
            out[key] = "package" if fn in package[dll] else "fail"
        else:
            try:
                h = ctypes.WinDLL(dll)._handle
            except OSError:
                out[key] = "fail"
                unresolved.append(key)
                continue
            ok = gp(h, ctypes.cast(ctypes.c_void_p(int(fn[1:])), ctypes.c_char_p) if fn.startswith("#") else fn.encode())
            out[key] = "standard" if ok else "fail"
            if not ok:
                unresolved.append(key)
r = json.load(open(path))
r["platform"] = out
json.dump(r, open(path, "w"))
print(collections.Counter(out.values()), unresolved)
