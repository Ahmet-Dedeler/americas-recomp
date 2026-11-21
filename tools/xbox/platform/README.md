# tools/xbox/platform

What the package's DLLs need from the platform, and stand-ins for the Xbox-only parts. Result summary: `docs/xbox/PLATFORM_SURFACE.md`.

* `pe_imports.py <dir> <out.json>` lists the static imports (normal and delay-load) and exports of every PE file under a directory.
* `make_platform_results.py <imports.json>` classifies each imported function (standard Windows / shipped in the package / needs a shim) and
  stores it under `"platform"` in `docs/xbox/results.json` for the progress map. Windows only.
* `xmem.cpp` is a stand-in `xmem.dll` (XMemAlloc, XMemFree, XMemGetWorkingSetStatistics, XMemVirtualQuery); `d3d12_xs.cpp` forwards the two
  `d3d12_xs.dll` functions the DLLs import to the PC `d3d12.dll`. Build each with `cl /LD /O2 file.cpp /Fe:name.dll`. Argument shapes were read from the callers' machine code;
  the meaning of the attribute bits and of two output fields is a guess (see the comments in `xmem.cpp`). `XMEM_LOG=<file>` logs every call.
* `bink_test.cpp` loads `Bink2Scarlett.dll` next to a stand-in `xmem.dll`, decodes every frame of a video and saves two frames as raw BGRA.
  `carve_bink.py <movies.rpf> <out_dir> [count]` extracts Bink videos from the archive (they are stored raw).

## First run with the game executable
1. `python exe_report.py <exe or directory> --out report --stubs` prints header facts (entry point, subsystem, sections) and classifies every
   static import: standard Windows, shipped in the package, covered by the stand-ins here, or *needs* an implementation. `report/needs.json`
   lists the last group; `report/stubs/` holds generated logging stand-in DLLs for it (`build.bat` from an x64 VS prompt).
2. Run the game with the stubs beside it and `STUB_LOG=<file>`: every call is logged as `dll!name(rcx, rdx, r8, r9) tid=..`
   (`STUB_ABORT=<text>` stops at the first call whose name contains the text). That trace says which functions are called and in which order.
   Check on the known case: a generated `xmem.dll` stub in front of `Bink2Scarlett.dll` logged `xmem!XMemAlloc(0x3c20, 0x5e50000, ..)`,
   the same size and attribute word found by disassembly.
3. Replace stubs with real implementations as behaviour becomes known (`xmem.cpp`, `d3d12_xs.cpp` are the pattern).
Static imports only: calls made through GetProcAddress, COM or vtables do not show up in step 1.

No game files are part of this repository: the DLLs, the videos and the decoded frames stay local.
