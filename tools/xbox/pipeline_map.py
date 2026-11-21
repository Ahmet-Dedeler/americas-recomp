"""Render-target format -> recompiler pixel-stage export settings (SPI_SHADER_COL_FORMAT value, export swizzle).

Why: the test harness (cpu_compare_ps.py / exec.cpp) hard-coded these and three of its defaults were wrong (see XBOX_SHADERS.md).
With the game executable, the real D3D12 pipeline state gives a DXGI format per render target; this turns it into the two values
`ShaderPixelStageInfo.targetOutputMode[i]` and `targetExportMapping[i]` want, so a draw's shader is translated with the right export packing.

Rules are from the public GCN/RDNA register documentation (SPI_SHADER_COL_FORMAT: 0 zero, 1 32_R, 2 32_GR, 3 32_AR, 4 FP16_ABGR,
5 UNORM16_ABGR, 6 SNORM16_ABGR, 7 UINT16_ABGR, 8 SINT16_ABGR, 9 32_ABGR) and the usual driver practice (blendable <=16-bit formats are
exported as FP16 pairs, integer formats keep integers, 32-bit float formats use the 32-bit exports). They are NOT verified against this
game: self_test() only checks the rules against themselves. The first real check is the executable's draws: compare the mode this module
predicts with the mode the shader's EXP instructions use (compressed export = mode 4/5/6/7/8, uncompressed = 1/2/3/9).
Export swizzle: identity (0xe4) unless the format lacks channels, in which case the missing ones are exported as zero (mode 0 per channel
is not expressible; the compressed/32-bit modes with a channel mask are used instead: R -> 32_R, RG -> 32_GR).
"""
ZERO, R32, GR32, AR32, FP16, UN16, SN16, UI16, SI16, ABGR32 = range(10)
IDENTITY = 0xE4

# DXGI_FORMAT name -> (channels, kind, bits per channel)
_FORMATS = {}
for name, ch, kind, bits in [
    ("R32G32B32A32_FLOAT", 4, "f", 32), ("R32G32B32A32_UINT", 4, "u", 32), ("R32G32B32A32_SINT", 4, "s", 32),
    ("R32G32_FLOAT", 2, "f", 32), ("R32G32_UINT", 2, "u", 32), ("R32G32_SINT", 2, "s", 32),
    ("R32_FLOAT", 1, "f", 32), ("R32_UINT", 1, "u", 32), ("R32_SINT", 1, "s", 32),
    ("R16G16B16A16_FLOAT", 4, "f", 16), ("R16G16B16A16_UNORM", 4, "n", 16), ("R16G16B16A16_SNORM", 4, "sn", 16),
    ("R16G16B16A16_UINT", 4, "u", 16), ("R16G16B16A16_SINT", 4, "s", 16),
    ("R16G16_FLOAT", 2, "f", 16), ("R16G16_UNORM", 2, "n", 16), ("R16G16_SNORM", 2, "sn", 16), ("R16G16_UINT", 2, "u", 16), ("R16G16_SINT", 2, "s", 16),
    ("R16_FLOAT", 1, "f", 16), ("R16_UNORM", 1, "n", 16), ("R16_SNORM", 1, "sn", 16), ("R16_UINT", 1, "u", 16), ("R16_SINT", 1, "s", 16),
    ("R8G8B8A8_UNORM", 4, "n", 8), ("R8G8B8A8_UNORM_SRGB", 4, "n", 8), ("B8G8R8A8_UNORM", 4, "n", 8), ("B8G8R8A8_UNORM_SRGB", 4, "n", 8),
    ("R8G8B8A8_SNORM", 4, "sn", 8), ("R8G8B8A8_UINT", 4, "u", 8), ("R8G8B8A8_SINT", 4, "s", 8),
    ("R8G8_UNORM", 2, "n", 8), ("R8G8_SNORM", 2, "sn", 8), ("R8G8_UINT", 2, "u", 8), ("R8G8_SINT", 2, "s", 8),
    ("R8_UNORM", 1, "n", 8), ("R8_SNORM", 1, "sn", 8), ("R8_UINT", 1, "u", 8), ("R8_SINT", 1, "s", 8),
    ("R10G10B10A2_UNORM", 4, "n", 10), ("R10G10B10A2_UINT", 4, "u", 10), ("R11G11B10_FLOAT", 3, "f", 11),
    ("B5G6R5_UNORM", 3, "n", 5), ("B5G5R5A1_UNORM", 4, "n", 5), ("B4G4R4A4_UNORM", 4, "n", 4),
    ("R9G9B9E5_SHAREDEXP", 3, "f", 9),
]:
    _FORMATS[name] = (ch, kind, bits)


def export_mode(dxgi_name: str) -> int:
    """SPI_SHADER_COL_FORMAT value for a render target of this DXGI format (name without the DXGI_FORMAT_ prefix)."""
    ch, kind, bits = _FORMATS[dxgi_name.replace("DXGI_FORMAT_", "")]
    if kind == "u":
        return UI16 if bits <= 16 else (R32 if ch == 1 else GR32 if ch == 2 else ABGR32)
    if kind == "s":
        return SI16 if bits <= 16 else (R32 if ch == 1 else GR32 if ch == 2 else ABGR32)
    if bits == 32:   # float32
        return R32 if ch == 1 else GR32 if ch == 2 else ABGR32
    if kind == "sn" and bits == 16:
        return SN16
    if kind == "n" and bits == 16:
        return UN16
    return FP16      # float16, 8-bit and packed formats: blendable, exported as FP16 pairs


def export_mapping(dxgi_name: str) -> int:
    """Value for ShaderPixelStageInfo.targetExportMapping[i]: the identity swizzle for every format here (the unwritten channels are
    simply never exported, or exported as junk the output merger ignores); kept as a function so a game-specific override can go here."""
    return IDENTITY


def stage_settings(formats):
    """formats: up to 8 DXGI names (None = unbound) -> (targetOutputMode[8], targetExportMapping[8])."""
    mode, mapping = [ZERO] * 8, [IDENTITY] * 8
    for i, f in enumerate(formats[:8]):
        if f:
            mode[i], mapping[i] = export_mode(f), export_mapping(f)
    return mode, mapping


def col_format_register(formats) -> int:
    """SPI_SHADER_COL_FORMAT as the register word (4 bits per target)."""
    return sum(m << (4 * i) for i, m in enumerate(stage_settings(formats)[0]))


def self_test():
    """Spot checks that follow from the documented rules (not a validation against the game)."""
    assert export_mode("R8G8B8A8_UNORM") == FP16 and export_mode("R16G16B16A16_FLOAT") == FP16
    assert export_mode("R32G32B32A32_FLOAT") == ABGR32 and export_mode("R32_FLOAT") == R32 and export_mode("R32G32_UINT") == GR32
    assert export_mode("R16G16_UINT") == UI16 and export_mode("R16_SINT") == SI16 and export_mode("R16G16B16A16_UNORM") == UN16
    assert col_format_register(["R8G8B8A8_UNORM", "R32G32B32A32_FLOAT", None]) == 0x94
    print("pipeline_map self-test ok:", len(_FORMATS), "formats")


if __name__ == "__main__":
    self_test()
