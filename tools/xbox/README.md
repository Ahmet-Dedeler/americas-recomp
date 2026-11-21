# tools/xbox

Experimental tooling for running Xbox Series shader code through the shader recompiler. See `docs/xbox/XBOX_SHADERS.md`.

1. `build_msvc.py` builds the recompiler and the tools below with MSVC (run it from PowerShell/cmd). On Linux,
   `cmake -S tools/xbox -B build-xbox -DCMAKE_BUILD_TYPE=Release && cmake --build build-xbox` builds `driver`, `stages` and `exec`
   (needs the `SPIRV-Headers` and `Vulkan-Headers` submodules and the Vulkan loader). Without `rdna2.py`, `rdna_asm.py` takes its opcode
   tables from LLVM (`rdna2_llvm.py`, needs `llvm-mc` with the AMDGPU target), so `exec_tests.py` and `exec_bvh_tests.py` run with no
   game files: `XBOX_EXEC=build-xbox/exec python3 tools/xbox/exec_tests.py`. `VK_DRIVER_FILES=<icd.json>` picks the GPU.
2. `extract_shaders.py` writes prologue-stripped, trimmed code blobs `<idx>.bin` (it depends on the RPF9
   extraction pipeline and `rdna2.py` decoder kept next to the dumped shaders; adjust its paths).
3. `driver.exe <code.bin> <wave 32|64> <out.spv> [user_data_dwords]` recompiles one compute shader with stubbed
   bindings (user-data pointer pairs backed by fake buffer-descriptor memory).
4. `run_batch.py` runs the driver over a directory of blobs and validates with `spirv-val`.
5. `stages.exe <code.bin>` times decode / CFG build / structurize separately (for the slow ray-tracing shaders).
6. `exec.exe <scenario.txt>` is the execution harness: it recompiles a compute shader against REAL Vulkan device memory, dispatches it on the
   host GPU and writes the memory regions (and images) back. Build it on Windows with `build_msvc.py --with-exec`.
   `rdna_asm.py` assembles small RDNA2 test programs (opcode numbers come from the decoder tables; each program is decoded back to check it),
   `exec_tests.py` and `exec_bvh_tests.py` hold the tests. Set `XBOX_EXEC` to the `exec.exe` path. Supported: guest buffers, shader data,
   flattened SRT, identity BDA page table and fault reporting, 2D RGBA32F images, samplers. Pixel shaders (`stage ps`, see the pixel
   smoke, interpolation and per-vertex tests): one triangle over up to eight RGBA32F targets; per-vertex inputs need
   `VK_KHR_fragment_shader_barycentric`. Not yet: other image formats/dimensions, image tables, texel buffers, other graphics stages.
   Compute shaders get the device's subgroup size for their wave size; a wave64 shader on a device whose largest compute subgroup is 32
   (NVIDIA, Intel) is recompiled for 32-wide subgroups, two guest lanes per invocation. `XBOX_HOST_SUBGROUP=<n>` forces the host size.

## Pixel smoke test

With the Vulkan SDK's `glslangValidator` on PATH and the execution harness built,
run this from the repository root:

```powershell
python tools/xbox/pixel_smoke.py --exec "build-xbox/exec.exe"
```

The vertex shader draws a full-screen triangle. The standalone RDNA2 pixel shader
writes the pixel-center X and Y coordinates, zero blue, and unit alpha to a
13 by 7 RGBA32F attachment. The test checks every channel at every coordinate
and reports the first mismatch. It uses no external shader corpus. The scenario's `export 0 9 0xe4` selects
the 32-bit ABGR export mode with the identity component mapping.

## Vertex color interpolation test

With a built execution harness and the Vulkan SDK's `glslangValidator` on PATH:

```powershell
python tools/xbox/interpolation_test.py --exec "build-xbox/exec.exe"
```

A GLSL vertex shader assigns red, green and blue to a triangle. Its vertices
have clip-space W values of 1, 2 and 4. The RDNA2 fragment program interpolates
each channel using VINTRP P1/P2 and exports RGBA32F. At every pixel center, the
CPU derives screen-space weights from the projected triangle, divides each by
the corresponding W, then normalizes their sum to one. Covered pixels must match
within 0.00001 per channel. Outside pixels must exactly retain the clear value.
The triangle edges avoid pixel centers, so the reference needs no edge tie-break.

## Per-vertex input test

With a built execution harness and the Vulkan SDK's `glslangValidator` on PATH:

```powershell
python tools/xbox/per_vertex_test.py --exec "build-xbox/exec.exe"
```

The fragment program reads the P0, P10 and P20 interpolation parameters of one
attribute with `VINTRP MOV` and writes them to three RGBA32F targets. The vertex
shader assigns red, green and blue to the three vertices. P10 and P20 are the
vertex-1 and vertex-2 plane coefficients (P1 - P0 and P2 - P0), not the raw
vertex colours. Every pixel must read P0 = (1, 0, 0, 1), P10 = (-1, 1, 0, 0) and
P20 = (-1, 0, 1, 0). The test needs `VK_KHR_fragment_shader_barycentric`; without
a geometry shader the host path is not a valid reference for per-vertex inputs.

## Quad swizzle test

With a built execution harness and the Vulkan SDK's `glslangValidator` on PATH:

```powershell
python tools/xbox/quad_test.py --exec "build-xbox/exec.exe"
```

The first fragment program exchanges each pixel with its horizontal and vertical
neighbour inside the 2x2 quad with `ds_swizzle_b32`, subtracts, and exports the
values and differences. The second narrows EXEC to one lane per quad, expands it
with `s_wqm_b32`, and exports a constant; a second run activates two quads only,
so the other quads must stay clear. Both run with `XBOX_NO_FRAGMENT_BARY=1`, the
host perspective path; the quad operations do not need the barycentric
extension.

## Windows compute smoke test

From the repository root in PowerShell, with Python 3, the x64 MSVC C++ toolchain,
the Vulkan SDK, and a Vulkan-capable GPU driver installed, run:

```powershell
git submodule update --init 3rdparty/SPIRV-Headers
python tools/xbox/build_msvc.py --with-exec
python tools/xbox/exec_smoke.py
```

The build reads `VULKAN_SDK`. Use `--vulkan-sdk "path to SDK"` to select another
installation. For a custom build directory, pass `--exec "path to exec.exe"` to
the smoke test. It generates a standalone RDNA2 compute program, dispatches one
32-lane workgroup, and checks all 1024 output bytes. A successful run prints
`PASS`. This test requires neither a shader corpus nor an external decoder.

### Test v_mad_legacy_f32 clamp

With the execution harness built, run:

```powershell
python tools/xbox/mad_legacy_clamp_test.py --exec "build-xbox/exec.exe"
```

One 32-lane workgroup computes `2.0 * 4.0 + 4.0`, `-2.0 * 4.0 + 4.0` and
`0.5 * 0.5 + 0.0` with `v_mad_legacy_f32` and the VOP3 clamp bit. The clamped
results must be 1.0, 0.0 and 0.25 in every lane. Clearing the clamp bit produces
12.0, -4.0 and 0.25, so the test fails when clamp is ignored.

### Test texture sampling

With the Vulkan execution harness built and the SDK's `glslangValidator` on PATH,
run `python tools/xbox/texture_test.py --exec "build-xbox/exec.exe"`.

A GLSL vertex shader supplies UVs and unequal clip-space W. An RDNA2 fragment
program interpolates U and V, samples a generated 4 by 3 RGBA32F texture at mip
level zero, and exports the result. The CPU calculates perspective-correct UVs
at each pixel center and independently selects the nearest texel with clamp-to-edge
addressing. All 12 texels must appear, all 192 covered pixels must match exactly,
and all 832 outside pixels must retain the clear value.
