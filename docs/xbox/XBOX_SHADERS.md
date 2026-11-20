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
| `Structurizer.cpp` | fast shared-merge split budget (default 60% of blocks, min 64 splits, `XBOX_SPLIT_PCT`), full 400% cascade as fallback; `XBOX_STRUCT_PROF=1` prints timing and attempts | see below |
| `Recompiler.cpp`, `SrtDescriptorEvaluation.cpp`, `SpirvSpecialization.cpp` | MSVC build fixes (`static constexpr`, no `__builtin_prefetch`, narrowing cast) | upstream builds with MinGW GCC only |

Everything is marked `XBOX-FORK` where it is a behaviour change.

## Results on 300 compute shaders (51 ray tracing + 249 random), wave 32, stubbed bindings

- 182 produce SPIR-V accepted by `spirv-val` (Vulkan 1.3); 0 invalid. (165 on the first pass + 17 of the 24 slow
  ray-tracing shaders once the structurizer fix below let them finish.)
- ~100 fail on image/sampler descriptor resolution: caused by the stub memory, not (known) ISA gaps.
- 2 ray-tracing shaders (23206, 23207; 3.7k blocks) still exceed a 15 minute limit in the structurizer.
- The valid ray-tracing modules are very large (0.6-1.9M SPIR-V words each).

## Not done / caveats

- Descriptor tables / Xbox root-signature ABI are not modelled. AnyPS5 assumes PS5 user-data conventions.
- Graphics shaders (PS/VS/GS) not tried.
- No execution or output comparison.

## Structurizer speed (ray-tracing loop graphs)

Profiling showed the slow ray-tracing shaders were not hung: `Structurizer` runs a cascade of up to 4 attempts
(8 with `relaxReturnJoins`); on these graphs attempts 1-3 each ran the whole split budget (4x the block count,
~1 block cloned per split, graph grows to 5x) and threw, and the 4th succeeded after ~0.16x splits. Each split
recomputes all analyses.

On a 4,147 shader corpus (all 2,647 compute + 1,500 other) no completed split loop needed more than 0.72x blocks
(0.37x for graphs over ~250 blocks). The cascade now runs with 60% (min 64) first and reruns with the original
400% only if that fails. Measured against the original budget:

- 4,119 shaders finish in both: identical structured-graph hash for all, no success/failure changed;
- 20 shaders that exceeded 90 s now finish (8 still exceed 90 s); slowest finishing shader 69 s -> 2 s;
- total time on shaders finishing in both: 593 s -> 274 s.

Remaining cost is the per-iteration search in `splitOneSelectionMerge` (~4 ms at ~1.5k blocks: every iteration
rescans all selection headers). Caching non-splittable headers across iterations would be the next step.
`tools/xbox/structurizer_corpus.py` + `stages.exe` reproduce the comparison (`stages` prints a graph hash).
