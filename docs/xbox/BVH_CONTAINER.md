# BVH container (core_bvh4.rpf, 8.3 GB) - structure survey

From the author's own console dump. The dump itself is not in this repository. Everything below is from reading headers and shader code; none of it was checked on hardware.
- Outer file: "9FPR" header, encrypted root TOC (not decoded). Contains 1,260 nested RPF9 archives (4 KiB aligned, found by magic) - they match the *_bvh.rpf names in out_bvh4/*.xml (terrain etc).
- Nested archive: 9FPR header + small encrypted TOC, then at a 4 KiB boundary: 16 zero bytes + zlib streams (78 da), 16-byte aligned, then a non-zlib trailer.
- 1,244 archives parsed (16 not: no stream start found). 473,612 streams, 7.0 GB compressed -> 118 GB raw (mostly zero padding, ~93% zero in the samples).
- Streams come in pairs: 208-byte "system" header (virtual pointers 0x50000000 base, 3x4 identity transform at dwords 20-34, data size at dword 42) + one large data segment (64 KiB multiples, sparse).
- Typical archive: 128 streams = 64 header/data pairs (357 archives). Others: 124/126/122..., or 2 (56 archives).
- NOT decoded: data segment layout (sparse, offset tables near the start, header words at 0x20 point near the end of the used area). A plain RDNA2 128-byte box-node scan finds nothing, so it is not raw ray-accelerator nodes, or is stored in another layout.
- survey.py/survey2.py/load.py here; survey2.json = per-archive stats.

## Cross-reference with XboxBvhBuilder shaders (out_bvhbuilder, `dxc -dumpbin blobNN.dxbc` gives readable DXIL)
- The builder has DecompressHeader, Box32Rehydration, Box16Rehydration, LeafRehydration, CopyAndCompactBVH, GetBVHCompactedSize: so the stored BVH is a COMPRESSED form that is rehydrated into the hardware layout at load time. This fits the bit-packed, byte-planar data in the segments, and explains why a plain 128-byte box-node scan found nothing.
- DecompressHeader: copies sizeToPreserveDiv16*16 header bytes, clears bit 24 (0x01000000) of header dword 0 (compressed flag), and writes 0xFFFFFFFF at dword[14] + ((((dword[12]>>3)<<6) - dword[13]) >> 6 << 2). Compact size = ((dword*8)+311)&~255; nodes are 64-byte units.
- The 208-byte per-BVH header: dwords 16-17 = unique 64-bit id, dwords 20-30 = 3x3 matrix (rows padded to 16 B, identity in most), dword 40 = used size (256 B aligned, <= data length), dword 41 = flags/counts. NOT yet matched to the BVH header the shaders read (the 0x30/0x34/0x38 fields), because the data segment's first 0x1c00 bytes are zero and the flagged header was not located.
- Data segment end of used area holds a 15-entry ascending byte-offset table followed by 9 (probably per-level/per-type start offsets).

## Box16/Box32 rehydration read (dxil/blob01.ll, blob02.ll via dxc -dumpbin) - from the shader code, NOT yet matched to the data
- cbuffer BoxRehydrationParams {offsetToHierarchy, offsetToParentData, offsetToLeaves, IsUpdatableBVH}. Resources: compressedBVH (src), decompressedBVH (dst). One 4096-byte group of the hierarchy stream per threadgroup; each thread reads one dword: low 24 bits = a mask (popcount used for a prefix sum), high 8 bits = a count (wave prefix/sum over it) - i.e. a packed topology stream.
- Node ids use the RDNA2 ray-accelerator encoding: low 3 bits = node type, (id>>3)<<6 = byte offset in 64-byte units. Type 0/1 = triangle node (3-4 float vertices at +0..+47, flags dword at +48, bit 0x80 = second triangle), type 4 = 16-bit-float box node (half AABBs), box nodes' AABBs are rebuilt by min/max over children (shared-memory gs_boxNodeAABBs, 1536 halves).
- So the stored form keeps leaves + a compact hierarchy and the box nodes are re-created ("rehydrated"). The 15-entry table at the end of the used area is probably cumulative 64-byte-node indices per tree level (differences 86,42,38,19,10,11,4,6,2,3,0,2,0,1, roughly halving), starting at 1477.
- Checked against data: leaf vertex data is not plain float triangles (only 2 candidate 64 B blocks in the sample; values like x.004/x.508 suggest quantised/fixed-point positions). LeafRehydration (blob03, ~4000 lines of DXIL) is the shader that defines the leaf encoding and has to be read next.

## LeafRehydration (blob03): leaves are NOT stored as vertices
- cbuffer LeafParams {translation, offsetToLeaves, scale, ScaleOrTranslateEnabled, rotationMatrixRow0..2, numElements}; SRV geometryInputs = D3D12_RAYTRACING_GEOMETRY_TRIANGLES_DESC[] (Transform3x4, IndexFormat, VertexFormat, IndexCount, VertexCount, IndexBuffer, VertexBuffer{StartAddress, StrideInBytes}).
- The shader reads the game's own vertex/index buffers (rawBufferLoad.i64 x48, legacyF16ToF32 x22, 129 integer bitfield ops) and writes the hardware triangle nodes. So the compressed BVH holds topology + references into geometry (indices/primitive ids, hence the small repeated integers), not vertex positions.
- Consequence: core_bvh4.rpf alone cannot yield triangles; the matching vertex/index buffers (other archives, e.g. the terrain meshes) are needed, plus the per-BVH transform (208-byte header dwords 20-30 look like the rotation rows).
