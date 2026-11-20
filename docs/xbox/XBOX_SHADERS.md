# Xbox Series shaders in the AnyPS5 recompiler (experimental fork notes)

Status of the `xbox-series` branch. Everything here comes from one title's `shaders.rpf` (RPF9 container,
24,804 shaders, all DXIL SM 6.6 stubs). Nothing was executed on hardware: "valid" means `spirv-val` accepts the output.

## Shader format (Xbox side, not handled by AnyPS5)

- Each shader is a DXBC container whose `DXIL` chunk is a stub (bitcode = the 4 bytes `NOBC`).
- The machine code is in an `XDAT` container, section `XBCH` (24-byte header + zlib stream) -> `FSSM` blob.
  Code range = u64 offset at +0xC0, u64 size at +0xC8 of the inflated blob.
- Code is RDNA2 (gfx10.3). 99.89% of all words are opcodes AnyPS5 already decodes; the rest are constant pools.
- 10-dword prologue in front of the code in 15,522 of 24,804 shaders: 4 header dwords, then two 12-byte
  VOP3-encoded words (op 0x180). Strip it (`extract_shaders.py` does) or the decoder reads junk.
- Constant pools / padding after the last `s_endpgm` (and some zero words inside) are NOT instructions;
  the recompiler decodes linearly, so cut the code at the last `s_endpgm`.
- Ray tracing uses the hardware ray accelerator: `image_bvh_intersect_ray` (MIMG op 230) and
  `image_bvh64_intersect_ray` (op 231), NSA form (nsa=3), R128=1. AnyPS5 decodes and emits these.

## Changes on this branch

| File | Change | Why |
|---|---|---|
| `RdnaVectorOpDecoder.cpp` | allow `clamp` on `v_mad_legacy_f32` (VOP3) | Xbox shaders use it; opcode was already supported, only the clamp bit was refused. Clamp semantics not verified. |
| `RdnaVectorOpDecoder.cpp` | allow op_sel bits 2-3 on `v_permlane16/x16_b32` | Xbox shaders set op_sel[3]. Assumed to have no effect on a 32-bit op. **Unverified.** |
| `RdnaVectorOpDecoder.cpp` | opcode/modifier numbers in two error messages | diagnostics |
| `RoundingMode.cpp` | ignore `s_setreg*` writes to hwreg 20/21 (`FLAT_SCRATCH_LO/HI`) | Xbox shaders zero these at entry. Ignored, so a shader that really uses scratch via flat_scratch would be wrong. |
| `Recompiler.cpp`, `SrtDescriptorEvaluation.cpp`, `SpirvSpecialization.cpp` | MSVC build fixes (`static constexpr`, no `__builtin_prefetch`, narrowing cast) | upstream builds with MinGW GCC only |

Everything is marked `XBOX-FORK` where it is a behaviour change.

## Results on 300 compute shaders (51 ray tracing + 249 random), wave 32, stubbed bindings

- 165 produce SPIR-V accepted by `spirv-val` (Vulkan 1.3); 0 invalid.
- ~100 fail on image descriptor resolution: caused by the stub memory, not (known) ISA gaps.
- 24 ray-tracing shaders are extremely slow in `Structurizer` (534-3,748 blocks; 534 blocks = 32 s; superlinear).
  Not a hang for the smallest; larger ones unmeasured.

## Not done / caveats

- Descriptor tables / Xbox root-signature ABI are not modelled. AnyPS5 assumes PS5 user-data conventions.
- Graphics shaders (PS/VS/GS) not tried.
- No execution or output comparison.
