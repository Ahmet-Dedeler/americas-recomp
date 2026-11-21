# Platform surface of the 16 DLLs in the package root (static imports only)
Tool: tools/xbox/platform/pe_imports.py (normal + delay-load imports; no delay-load imports were present). Per-function classification: the "platform" key of docs/xbox/results.json (make_platform_results.py).

| Category | DLLs | Functions |
|---|---|---|
| Standard Windows (resolves on this PC: every one of the 492 functions found with GetProcAddress) | 61 (55 api-sets + 6 others) | 492 |
| Provided by the package itself (msvcp140, vcruntime140, vcruntime140_1; every imported name is exported by the package copy) | 3 | 113 |
| Xbox-specific, no PC equivalent file | 2 (xmem.dll, d3d12_xs.dll) | 6 distinct |

Xbox-specific imports:
- xmem.dll: XMemAlloc, XMemFree (Bink, upscaler), XMemGetWorkingSetStatistics (upscaler), XMemVirtualQuery (XboxBvhBuilder)
- d3d12_xs.dll: D3D12SerializeVersionedRootSignature (XboxBvhBuilder), D3D12CreateRootSignatureDeserializer (upscaler); both exist with the same names in the PC d3d12.dll

Shims (tools/xbox/platform/xmem.cpp, d3d12_xs.cpp): all six DLLs that import something (XboxBvhBuilder, both FidelityFX, Bink, XCurl, libHttpClient.GDK) load on this PC with them; Bink decodes a real video (tools/xbox/platform/bink_test.cpp; the video was carved with carve_bink.py).
Argument shapes for the three less common xmem calls come from disassembling the callers; the meaning of the attribute bits, of the working-set struct and of the +0x30 field of XMemVirtualQuery is a guess.
Static imports only: this does not include what the DLLs call through COM/vtables (D3D12 device methods) or what the game executable imports.
