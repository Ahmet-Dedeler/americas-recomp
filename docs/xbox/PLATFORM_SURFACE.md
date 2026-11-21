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

## XboxBvhBuilder.dll on a PC device (tools/xbox/platform/bvh_test.cpp)
`bvh_test.exe` loads the DLL next to the stand-in `xmem.dll` / `d3d12_xs.dll`, creates a PC D3D12 device (AMD Radeon RX 7800 XT, Device11,
raytracing tier 1.1, shader model 6.8) and constructs the `XboxBvhBuilder(ID3D12Device11*, bool)` class through its exported constructor.
What the first run showed, in order:
1. The constructor throws `E_INVALIDARG`. The cause is its first root signature: its flags are `0x3000000`, bits the PC runtime does not
   define (the PC flags are all in the low 16 bits), so the PC serializer rejects it. `d3d12_xs.dll` now clears the high byte on a copy of the
   descriptor (what the bits mean is not known; `XS_KEEP_XBOX_FLAGS=1` forwards it unchanged, `XS_LOG=<file>` logs every serialize call with
   the parameters and the runtime's result). The signature is plain: root SRV, root UAV, 16 root constants.
2. The constructor then creates seven compute pipelines (embedded shader blobs of 0x1410, 0x1ec4, 0x1580, 0x493c, 0x752c, 0x1118 and 0xfe8
   bytes) through a device method at vtable slot 62 (`+0x1f0`), called with the pipeline description, two zero arguments, the interface id and
   the output pointer. On a PC device slot 62 is `ID3D12Device5::CreateStateObject(desc, riid, ppv)`, which takes three arguments, so this is
   not the PC method: the Xbox `ID3D12Device` vtable has methods the PC one does not, and the call crashes inside the PC runtime
   (fail-fast, the process dies). So the builder needs a device object with the Xbox vtable layout, forwarding to the PC device. That
   proxy is the "D3D12.X runtime" row of the progress map, and the game executable will use many more of those slots.
Not reached: any build or rehydrate call. Those take GPU virtual addresses, and the builder's own shaders are DXIL that read memory by 64-bit
address, which has no PC D3D12 equivalent, so running them on a PC device needs the shader path decided first (see XBOX_SHADERS.md).
