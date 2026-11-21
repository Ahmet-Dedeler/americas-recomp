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
`bvh_test.exe` loads the DLL next to the stand-in `xmem.dll` / `d3d12_xs.dll`, creates a PC D3D12 device and constructs the
`XboxBvhBuilder(ID3D12Device11*, bool)` class through its exported constructor, handing it an Xbox-layout device stand-in
(`xbox_device_proxy.h`, see below). In order:
1. The first root signature carries flags `0x3000000`, bits the PC runtime does not define (its flags are all in the low 16 bits), so the PC
   serializer fails with `E_INVALIDARG`. `d3d12_xs.dll` clears the high byte on a copy of the descriptor (meaning of the bits unknown;
   `XS_KEEP_XBOX_FLAGS=1` forwards it unchanged, `XS_LOG=<file>` logs every serialize call).
2. The constructor then creates its 53 compute pipelines through device vtable slot 62, called as (description, 0, 0, interface id, output).
   On a PC device slot 62 is `CreateStateObject` (three arguments), so called directly it crashes inside the PC runtime. The stand-in
   maps slot 62 to `CreateComputePipelineState(description, iid, output)`; the description is a plain `D3D12_COMPUTE_PIPELINE_STATE_DESC`.
3. **WARP**: all 53 pipelines are created (`S_OK`, shader blobs from 4 KB to 30 KB), the constructor returns, and
   `GetRaytracingAccelerationStructurePrebuildInfo` answers for a 3,000-vertex triangle geometry: result 204,800 bytes, scratch 128,704
   (the PC driver's own answer for the same input: 115,984 / 124,560, so the layouts differ as expected). The destructor runs.
   **AMD Radeon RX 7800 XT**: the driver's shader compiler (`amdxc64.dll`) crashes on most of the builder's shaders. Compiling each of the 53
   blobs alone (`BVH_ONLY=n`): 9 compile, 39 crash the compiler (access violation inside `amdxc64.dll`), 5 fail with an error (one message:
   "Unknown pointer operation encountered during ParseAddress"); after a crash the device is removed, so they have to be run one per process.
   WARP creating the pipelines does not show that they compile: it may defer that.
   **Cause**: not a random driver bug. The shaders are Xbox DXIL: 44 of the 53 call non-standard functions `@xbox.*` (declared with opaque
   `i32` opcode first arguments, written by the Xbox DXC, `dxcxbox`), and those 44 are exactly the 44 that fail; the 9 that compile have
   none (`ResetCounterValues`, `CopyForBVHUpdate`, `ResetToIndexBuffer`, `CopyCounters`, `RadixReset`, `PT_ConstructBVH4`, `ResetKDOP`,
   `DecompressHeader`, `CopyAndCompactBVH`). The intrinsics are one-to-one wrappers of RDNA instructions: `global_load_dword/x2/x3/x4_with_cache`,
   `global_store_*`, `global_atomic_{add,sub,fmin,fmax}` and `s_load_dword_with_cache` (all with a 64-bit address operand), `Med3/Min3/Max3`
   (float and unsigned), `BFI`, `V_ALIGNBYTE_B32`, `V_CNDMASK_B32`, `V_PK_MIN/MAX_F16`, and lane operations (`ReadLane`, `LaneSwizzle`,
   `Ballot64`, `MBCNT64`, `S_BCNT1_U64`, `GetLaneID`, `WaveMin_U32`, `MakeUniform`, `TypedLoad`). 33 distinct names (the first `i32`
   argument looks like an instruction number, e.g. 356 for the dword global load). `tools/xbox/platform/bvh_shader_scan.py` classifies the
   shaders, `docs/xbox/bvh_builder_shaders.json` has the per-shader result:
   | group | shaders |
   |---|---:|
   | standard DXIL only (compile) | 9 |
   | needs raw 64-bit address access (global load/store/atomic) | 31 |
   | wave/lane intrinsics, no pointers (RadixCount, PrefixSum*, RadixAlloc) | 9 |
   | arithmetic intrinsics only (ConstructBVH4, BuildApexPointMap, Box16/32Rehydration) | 4 |
   So a PC path needs the 13 pointer-free shaders' intrinsics lowered to standard DXIL (all have direct equivalents: wave ops, min/max, bitfield
   insert, funnel shift), and for the 31 others an address-to-resource mapping for the 64-bit addresses, which the Xbox2Windows-style
   translation or our own RDNA-aware path would have to provide. The ISA semantics of these intrinsics are the ones the SPIR-V recompiler
   already implements for the same instructions.
Not reached: any build or rehydrate call. They take GPU virtual addresses, which the builder's DXIL shaders read by 64-bit address (no PC
D3D12 equivalent), and real inputs would also need the matching vertex/index buffers (BVH_CONTAINER.md).

## FidelityFX upscaler, unmodified, on a PC device (tools/xbox/platform/ffx_test.cpp)
The package's own `amd_fidelityfx_loader_xbox.dll` and `amd_fidelityfx_upscaler_xbox.dll` run as they are (they are ordinary x64 PE files;
nothing is recompiled), next to the stand-in `xmem.dll` / `d3d12_xs.dll`:
* `ffxQuery` (version enumeration, null context): one upscaler, id `0xf5a5ca1e01001001`, name `4.1.1`.
* `ffxCreateContext` for the upscale effect (type `0x10000`, backend extension type `0x01000002` carrying the device), 1280x720 to 1920x1080,
  returns 0 and `ffxDestroyContext` returns 0, on the RX 7800 XT and on WARP, debug layer: 0 errors, 3 warnings (buffers ignore the initial state).
* The create flags must be `0x29`: the descriptor validator (RVA `0xbb10`) fails with 6 unless bits 0x1, 0x8 and 0x20 are set and rejects
  0x2, 0x4, 0x40, 0x100 and 0x200 as unsupported.
* The provider creates 14 resources: 10 default-heap ones through device slot 76 (1920x1080 RGBA8, two RGBA16F, one RGB9E5, an 8-byte
  buffer, 1x1 RG32F, 1x1 R32_UINT, 80x45 R32F, a 21,045,760-byte buffer, a 131,072-byte buffer) and 4 upload buffers through slot 27.
Where the Xbox device differs from the PC one, as found by running it:
| Xbox call | PC | handling |
|---|---|---|
| device slot 76 (resource creation, heap properties / heap flags / description / state / clear value / iid / output) | slot 27 is CreateCommittedResource; slot 76 is something else | forwarded to CreateCommittedResource; the description is 56 bytes plus extension words and its layout field is `0x101` (tiled) or `0x1` (row-major buffer), replaced by driver-chosen layout |
| descriptor heap `GetCPU/GPUDescriptorHandleForHeapStart` (slots 9, 10) | returns through a hidden pointer in RDX | the Xbox build returns the 8-byte handle in RAX; heaps are wrapped so the call is made the PC way |
| slots 1, 15, 18, 19, 27, 38 on the device | same | forwarded unchanged (AddRef, descriptor increment size, SRV, UAV, committed resource for uploads, copyable footprints) |
This is the same finding the Xbox2Windows project reports (slot 76, layout `0x101`), reached here by running the code instead of translating it.
No dispatch was run: it needs real textures, a command list and the package's shaders.

`xbox_device_proxy.h` is the reusable part: an object whose vtable forwards every slot to the PC object by generated thunks (with per-slot hit
counters, `PrintHits`) and lets selected slots be replaced. It is the starting point for the "D3D12.X runtime" row.
