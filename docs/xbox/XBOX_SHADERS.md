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

- Descriptor tables: `tools/xbox/bindings.py` derives a per-shader table layout from the s_load/MIMG/MUBUF instructions (straight-line scan, approximate) and `driver.exe <bin> <wave> <out> <bind>` builds matching fake memory (image dim from the instruction). With this, 172/300 validate at a 30 s limit (+8 slow BVH shaders), 23 gained vs the flat stub, 7 regressed. the bindless (buffer-indexed) cases are handled by the runtime table path, see "Bindless descriptors" below (271/300). Root-signature ABI itself is still not modelled.
- Graphics shaders (PS/VS/GS) not tried.
- No execution or output comparison.

## Bindless descriptors (waterfall loops)

The recompiler already had a runtime descriptor-table path (`TableColumn`: an image/sampler descriptor selected by an
index read from a buffer or pointer). Most Xbox "bindless" shaders did not match it because of how the index reaches the
`s_load`: `v_readfirstlane` inside a waterfall loop (`v_cmpx_eq` narrows exec each iteration), then
`s_load_dwordx8 s[16:23], s[10:11], s16` (heap pointer in a user SGPR, byte offset in an SGPR).

Two changes (marked `XBOX-FORK`):

- `ActiveMaskImplies` now looks through Phis (loop-carried masks): a Phi implies a condition when all incoming values do; a
  path back to a Phi under examination counts as implied. This is sound (the loop only narrows the mask).
- `MatchAffineOffset` takes the "lane was active at the write" arm of a `Select(((exec >> (lane & 31)) & 1) != 0, new, old)`
  under `ReadFirstLane`. **This is an assumption, not a proof**: showing the waterfall mask is a subset of the write-time exec
  needs exec-flow analysis across different exec snapshots. It is the value the shader intends, but a shader that
  deliberately reads a stale lane would be mistranslated. `XBOX_NO_LANE_ASSUME=1` disables it.

- When the heap offset is an opaque wave-uniform value (e.g. a handle-validity test selecting between a loaded index and a
  fallback), an address heap column now uses the descriptor size as record size (32 bytes for an image) with the whole offset
  as key. The emitter already indexes the table by the byte offset, so only the record size was missing. **Assumption**: heap
  entries are size-aligned; an unaligned offset falls outside the table at run time. `XBOX_NO_OPAQUE_HEAP_INDEX=1` disables it.

Result on the 300-shader set with the binding model: 172 -> 200 (Phi + lane-active) -> 271 valid (`spirv-val`), 0 lost,
0 invalid. Remaining: 10 timeouts at the 30 s limit (slow BVH shaders, two more than before because they now get further),
8 `EvaluateRuntimeSources` failures, 4 Phi-chain descriptors, 2 atomic image format 56, 5 single oddities.
Output remains unexecuted; "valid" means only `spirv-val`, and the three assumptions above are untested against hardware.

## Full compute corpus (2,647 shaders, wave 32, 30 s limit, binding model)

`tools/xbox/run_corpus_bind.py` (needs `bindings.py` output for `bins_all`). Two fake-memory models, because the fake memory
decides what the recompiler sees when a shader derives a descriptor from constant-buffer contents:

| model | valid (`spirv-val`) | invalid |
|---|---|---|
| fake buffers unbacked | 2,336 | 0 |
| fake buffers backed by 64 KB of zeros | 2,409 | 0 |
| compiles under either | **2,472 (93.4%)** | 0 |

Each model rejects shaders the other accepts (63 lost / 136 gained going to the backed model): zeros read as a descriptor give
"incompatible image interface" or "unsupported buffer type". So these are properties of the test memory, not the shaders.
Realistic constant-buffer contents are needed to judge shaders that build descriptors from buffer data.
Remaining 175: 74 timeouts at 30 s, 101 failures (image interface/zeros, address-loaded or Phi-chain descriptors,
16 atomic image format 56). Nothing was executed.

Driver fake-buffer contents are selectable (`driver <bin> <wave> <out> <bind> <variant>`): 0 unbacked, 1 zeros, 2 tiled
well-formed 2D image T# records (32 B, format 56), 3 tiled well-formed buffer V# records (16 B). `tools/xbox/run_variants.py`
runs the shaders not yet valid under variants 2 and 3: only 8 more pass (167 still fail, 0 invalid). Union over the four
models: **2,480 of 2,647 (93.7%)**. The remaining failures are structural, not test-memory artefacts: 66 timeouts at 30 s,
descriptors loaded from computed addresses (35) or Phi chains (44), 18 atomic-image format failures.

## Graphics stages (first sample; wave 32, 30 s limit, binding model, fake buffers zero-backed)

`driver <bin> <wave> <out> <bind> 1 <ps|vs|ms> [inputAddr] [interpolators] [fp16mask]`, runners `run_graphics.py`, `run_mesh.py`.
**Everything the shader blob does not state is a default** (no register state is parsed; the FSSM header has an attribute table at
+0x130 that is not decoded). Defaults: float mode 0xc0 (fp32 denorm flush, fp16/64 preserve, DX10 clamp; the shader's own
`s_setreg` then ORs in its MODE bits), pixel inputs = perspective center only, export target i -> i, mode 4, no blend/merge state;
interpolant count and FP16 interpolants are derived from the `v_interp*` instructions; mesh = triangle list, 128-thread group,
42 primitives / 126 vertices per group, ESGS item size 4. Target capabilities include ClipDistance/CullDistance/MeshShadingEXT.

| stage | total | valid (`spirv-val`) | invalid |
|---|---|---|---|
| **pixel (headline, strict)** | 738 | **375** | 0 |
| pixel, `XBOX_ALLOW_UNMEASURED_F16_INTERP=1` (approximation, off by default) | 738 | 649 | 0 |
| vertex | 114 | 92 | 0 |
| mesh | 640 | 559 | 0 |

The pixel relaxation is an **approximation**: Xbox pixel shaders set fp16/fp64 rounding to round-toward-zero in MODE, and upstream
refuses 16-bit interpolation under non-default rounding ("not measured"). With the opt-in the rounding bits are ignored, so f16
interpolants may differ in the last bit. Remaining failures: pixel 89 (56 atomic-image format, 30 emulated barycentric inputs
over the location limit, 3 descriptor chains); vertex 21 (18 computed `s_swappc`/`s_setpc` targets the recompiler cannot resolve
statically, 2 Phi dominance, 1 sampler chain; 1 timeout); mesh 77 (66 descriptor sources reading through a null pointer, likely
a user-data gap in the harness, 8 sampler chains, 3 Phi dominance; 4 timeouts). None of this was executed, no pipeline state
was checked, and the mesh configuration is a guess, so a mesh shader passing here means only that the code translates.

### FSSM header (partly decoded, `tools/xbox/fssm_header.py`)

Verified against the code: the section directory at 0x120-0x130 and the pixel input table at 0x138 (8-byte entries: semantic,
component mask, index, slot, flags). The table's entry count equals the number of interpolants the code reads in 719 of 738
pixel shaders; the code's `v_interp` attribute number is the entry's position, and flag bit 4 marks 16-bit interpolants
(736/738; bit 3 covers all 738 but also flags some 32-bit ones). Driving the recompiler from the table instead of from the
instructions gives identical results on all 738 pixel shaders (375 valid either way). So the table adds no new information for
the interpolants themselves; it confirms the code-derived defaults.

Not decoded: the trailing sections (the directory points past the code), the register-like words at 0xd4/0xf8/0xfc/0x110/0x12c,
flag values 1/9/10, the vertex/mesh output tables near 0x1d0. Their code-offset-like fields do not land on instruction
boundaries, so they are not per-instruction annotations. No pipeline state (input enable, export formats, MODE) was found,
so the defaults above for those remain defaults.

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

A second change targets the large graphs: `joinWithinIteration` and `iterationSelectionMerge` allocated a
count x count bit matrix per call (~24,000 calls on a 1.4k block shader) and converted every set to an id list.
`densePostDominators` runs the same fixpoint over only the blocks that reach the join point (dense indices; the
sets are subsets of that domain, so the greatest fixpoint is identical). Same graph hash on all 4,119 shaders;
a 1,372-block shader went 15.3 s -> 8.1 s.

A per-scan cache of reachability sets was tried and removed: identical output, no measurable speedup.

Remaining cost per large shader is spread out: analysis recompute (~0.8 ms per split at 1.8k blocks), the
`findSelectionMerge` scan over all selection headers (~2 s of 8 s) and `splitOneLoopMerge`.
`tools/xbox/structurizer_corpus.py` + `stages.exe` reproduce the comparison (`stages` prints a graph hash).

## Ray-tracing traversal shaders

64 of the 2,647 compute shaders contain `image_bvh_intersect_ray` / `image_bvh64_intersect_ray` (counted by decoding instructions, not by scanning raw words, which gives false positives). AnyPS5 already emulates that instruction in SPIR-V by reading the BVH nodes from memory (triangle nodes 0/1, box16 type 4, box32 type 5), and its node layouts match what the Xbox builder's rehydration shaders write.

| Result | Shaders |
|---|---|
| valid at the 30 s limit | 55 |
| valid with a 15 min limit (100 to 456 s each) | 7 |
| error: descriptor chain (`CS_RTShadowTrace32HQ_Tiled`) | 1 |
| error: sampler chain (`CS_RTShadowTrace32TemporalHQ_OneOver8RPP`) | 1 |

62 of 64 pass `spirv-val`. The traversal loop has never been run, so this says nothing about correctness. A BVH in the hardware node format has to exist in memory; `XboxBvhBuilder.dll` ships its 52 builder shaders as DXIL only (no RDNA2 code), so they would need a separate DXIL path.

The stored BVH format is described in [BVH_CONTAINER.md](BVH_CONTAINER.md).

