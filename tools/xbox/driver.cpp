#include "Recompiler.hpp"
#include <array>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <algorithm>
#include <cstring>
#include <map>
#include <string>
#include <vector>
using namespace ShaderRecompiler;
// usage: driver <bin> <wave> <out.spv> [bindings.bind]
int main(int argc, char** argv) {
    if (argc < 4) return 2;
    std::ifstream f(argv[1], std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(f)), {});
    std::vector<std::uint32_t> code(raw.size() / 4);
    std::memcpy(code.data(), raw.data(), code.size() * 4);
    const std::uint32_t wave = std::stoul(argv[2]);
    // argv[5]: fake buffer contents. 0 = unbacked, 1 = zeros, 2 = tiled 2D image T# records, 3 = tiled buffer V# records (default 1)
    const int variant = argc > 5 ? std::atoi(argv[5]) : 1;
    RecompileRequest request{};
    const std::size_t nud = 16;
    std::vector<std::uint32_t> userData(nud);
    struct Node { std::uint32_t id = 0; std::int64_t parent = -1; std::uint32_t off = 0; std::vector<std::uint32_t> mem = std::vector<std::uint32_t>(0x10000); std::uint32_t base = 0; };
    std::map<std::uint32_t, Node> nodes;
    std::vector<std::pair<std::uint64_t, std::vector<std::uint32_t>>> bufferBacking;  // zeroed memory behind each fake V# base
    std::map<std::pair<std::uint32_t, std::uint32_t>, int> descs;  // (node,offset) -> class, higher wins: img 3 > buf 2 > samp 1
    if (argc > 4) {
        std::ifstream bf(argv[4]);
        std::string kind;
        while (bf >> kind) {
            if (kind == "node") {
                std::uint32_t id; std::string how; bf >> id >> how;
                Node& n = nodes[id];
                n.id = id; n.base = 0x10000000u + 0x100000u * id;
                if (how == "user") { std::uint32_t k; bf >> k; if (k < nud) userData[k] = n.base; }
                else { std::int64_t p; std::uint32_t off; bf >> p >> off; n.parent = p; n.off = off; }
            } else if (kind == "desc") {
                std::uint32_t id, off; std::string cls; bf >> id >> off >> cls;
                const int rank = cls.rfind("img",0)==0 ? 3 : cls == "buf" ? 2 : 1;
                auto& slot = descs[{id, off}];
                if (rank > (slot & 0xff)) slot = rank | (rank == 3 ? (cls[3]-'0') << 8 : 0);
            }
        }
        for (auto& [id, n] : nodes) {
            if (n.parent >= 0 && nodes.count(static_cast<std::uint32_t>(n.parent)) && n.off / 4 + 1 < nodes[static_cast<std::uint32_t>(n.parent)].mem.size()) {
                nodes[static_cast<std::uint32_t>(n.parent)].mem[n.off / 4] = n.base;
                nodes[static_cast<std::uint32_t>(n.parent)].mem[n.off / 4 + 1] = 0;
            }
        }
        std::uint64_t counter = 0;
        for (auto& [key, rank] : descs) {
            
            auto it = nodes.find(key.first);
            if (it == nodes.end()) continue;
            auto& m = it->second.mem;
            const std::size_t w = key.second / 4;
            const int dim = rank >> 8; rank &= 0xff;
            if (rank == 3 && w + 8 <= m.size()) {
                const std::uint64_t base = 0x40000000ull + 0x10000ull * counter++;
                const std::uint32_t d[8] = {static_cast<std::uint32_t>(base >> 8), static_cast<std::uint32_t>((base >> 40) & 0xffu) | (56u << 20) | (3u << 30), 15u | (15u << 14), 0xfacu | ((8u + dim) << 28), 0, 0, 0, 0};
                std::copy(d, d + 8, m.begin() + w);
            } else if (rank == 2 && w + 4 <= m.size()) {
                const std::uint64_t base = 0x50000000ull + 0x10000ull * counter++;
                const std::uint32_t d[4] = {static_cast<std::uint32_t>(base), static_cast<std::uint32_t>((base >> 32) & 0xffffu) | (16u << 16), 0x10000u, 0x30027facu};
                std::copy(d, d + 4, m.begin() + w);
                if (variant != 0) {
                    std::vector<std::uint32_t> mem(0x4000);
                    if (variant == 2) {  // tile with 32-byte 2D image T# records (format 56, as above)
                        const std::uint64_t ib = 0x40000000ull;
                        const std::uint32_t t[8] = {static_cast<std::uint32_t>(ib >> 8), static_cast<std::uint32_t>((ib >> 40) & 0xffu) | (56u << 20) | (3u << 30), 15u | (15u << 14), 0xfacu | (9u << 28), 0, 0, 0, 0};
                        for (std::size_t k = 0; k + 8 <= mem.size(); k += 8) std::copy(t, t + 8, mem.begin() + k);
                    } else if (variant == 3) {  // tile with 16-byte buffer V# records pointing at the first fake buffer
                        const std::uint32_t v[4] = {0x50000000u, 16u << 16, 0x10000u, 0x30027facu};
                        for (std::size_t k = 0; k + 4 <= mem.size(); k += 4) std::copy(v, v + 4, mem.begin() + k);
                    }
                    bufferBacking.push_back({base, std::move(mem)});
                }
            }
        }
    } else {
        for (std::size_t k = 0; k + 1 < nud; k += 2) { userData[k] = 0x10000000u + 0x100000u * static_cast<std::uint32_t>(k / 2); }
    }
    std::vector<MemoryRegion> regions;
    for (auto& [id, n] : nodes) regions.push_back({n.base, std::as_bytes(std::span<const std::uint32_t>(n.mem))});
    for (auto& [base, mem] : bufferBacking) regions.push_back({base, std::as_bytes(std::span<const std::uint32_t>(mem))});
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
