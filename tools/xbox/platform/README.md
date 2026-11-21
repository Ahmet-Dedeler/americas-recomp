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

No game files are part of this repository: the DLLs, the videos and the decoded frames stay local.
