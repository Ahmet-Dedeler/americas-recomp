#include "Recompiler.hpp"
#include <array>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
using namespace ShaderRecompiler;
// usage: driver <bin> <wave> <out.spv> [userdata_dwords]
int main(int argc, char** argv) {
    if (argc < 4) return 2;
    std::ifstream f(argv[1], std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(f)), {});
    std::vector<std::uint32_t> code(raw.size() / 4);
    std::memcpy(code.data(), raw.data(), code.size() * 4);
    const std::uint32_t wave = std::stoul(argv[2]);
    RecompileRequest request{};
    const std::size_t nud = argc > 4 ? std::stoul(argv[4]) : 16;
    std::vector<std::uint32_t> userData(nud);
    const std::size_t nreg = nud / 2;
    std::vector<std::vector<std::uint32_t>> blocks(nreg, std::vector<std::uint32_t>(0x4000));
    std::vector<MemoryRegion> regions;
    for (std::size_t k = 0; k < nreg; ++k) {
        const std::uint32_t base = 0x10000000u + 0x100000u * static_cast<std::uint32_t>(k);
        userData[2 * k] = base;
        userData[2 * k + 1] = 0;
        auto& b = blocks[k];
        for (std::size_t i = 0; i + 3 < b.size(); i += 4) { b[i] = 0x20000000u + 0x10000u * static_cast<std::uint32_t>(i); b[i + 1] = 0x00100000u; b[i + 2] = 0x4000u; b[i + 3] = 0x00027facu; }
        regions.push_back({base, std::as_bytes(std::span<const std::uint32_t>(b))});
    }
    request.context.memory = MemoryRegionView(std::span<const MemoryRegion>(regions));
    const std::vector<std::uint32_t> caps{1u,9u,10u,11u,22u,39u,61u,62u,63u,64u,65u,66u,67u,68u,4427u,4433u,4434u,4435u,4436u,4437u,4438u,4445u,4447u,4448u,4449u,4450u,5345u,5346u,5347u,5301u,5302u,5303u,5304u,5305u,6u,38u,41u,5u,17u,18u,19u,3u,2u,4u,7u,8u,45u,5249u};
    const std::array<std::string_view, 6> ext{"SPV_KHR_storage_buffer_storage_class", "SPV_KHR_physical_storage_buffer", "SPV_KHR_16bit_storage", "SPV_KHR_8bit_storage", "SPV_KHR_vulkan_memory_model", "SPV_KHR_shader_ballot"};
    request.shader = {ShaderStage::Compute, 0x20000u, code, 0, {}};
    request.context.waveSize = wave;
    request.context.userDataBaseRegister = 0;
    request.context.userData = userData;
    request.context.compute = ShaderComputeStageInfo{{wave, 1u, 1u}, 0u, {true, true, true}, false, 1u};
    request.target.vulkanVersion = 0x00403000u;
    request.target.spirvVersion = 0x00010600u;
    request.target.subgroupSize = wave;
    request.target.bdaAbiVersion = 1;
    request.target.supportedCapabilities = caps;
    request.target.supportedExtensions = ext;
    request.target.maxWorkgroupSize = {1024u, 1024u, 64u};
    request.target.maxWorkgroupInvocations = 1024;
    request.target.maxWorkgroupSharedMemoryBytes = 65536;
    request.layout = {0, 0, 0, 128};
    request.useCache = false;
    try {
        const auto result = Recompile(request);
        if (result.spirv.empty()) { std::printf("EMPTY\n"); return 1; }
        std::ofstream o(argv[3], std::ios::binary);
        const auto words = result.spirv.Words();
        o.write(reinterpret_cast<const char*>(words.data()), words.size() * 4);
        std::printf("OK %zu\n", words.size());
        return 0;
    } catch (const std::exception& e) {
        std::string m = e.what();
        for (auto& c : m) if (c == '\n' || c == '\r') c = ' ';
        std::printf("THROW %s\n", m.c_str());
        return 1;
    }
}
