# tools/xbox

Experimental tooling for running Xbox Series shader code through the shader recompiler. See `docs/xbox/XBOX_SHADERS.md`.

1. `build_msvc.py` builds the recompiler and the tools below with MSVC (run it from PowerShell/cmd).
2. `extract_shaders.py` writes prologue-stripped, trimmed code blobs `<idx>.bin` (it depends on the RPF9
   extraction pipeline and `rdna2.py` decoder kept next to the dumped shaders; adjust its paths).
3. `driver.exe <code.bin> <wave 32|64> <out.spv> [user_data_dwords]` recompiles one compute shader with stubbed
   bindings (user-data pointer pairs backed by fake buffer-descriptor memory).
4. `run_batch.py` runs the driver over a directory of blobs and validates with `spirv-val`.
5. `stages.exe <code.bin>` times decode / CFG build / structurize separately (for the slow ray-tracing shaders).
6. `exec.exe <scenario.txt>` is the execution harness: it recompiles a compute shader against REAL Vulkan device memory, dispatches it on the
   host GPU and writes the memory regions (and images) back. Build it like `driver.exe` plus the Vulkan SDK include path and `vulkan-1.lib`.
   `rdna_asm.py` assembles small RDNA2 test programs (opcode numbers come from the decoder tables; each program is decoded back to check it),
   `exec_tests.py` and `exec_bvh_tests.py` hold the tests. Set `XBOX_EXEC` to the `exec.exe` path. Supported: guest buffers, shader data,
   flattened SRT, identity BDA page table and fault reporting, 2D RGBA32F images, samplers. Not yet: other image formats/dimensions, image
   tables, texel buffers, graphics stages.

### Test vertex color interpolation

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
