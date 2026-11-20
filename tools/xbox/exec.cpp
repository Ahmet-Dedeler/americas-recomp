// XBOX-FORK: execution harness. Recompiles one RDNA2 compute shader against REAL device memory, dispatches it through Vulkan and
// writes the memory regions back, so recompiled code can be checked against an independent expectation (see tools/xbox/exec_tests.py).
//
// usage: exec <scenario.txt>
// scenario lines (all numbers decimal or 0x hex):
//   wave <32|64>                      groups <x> <y> <z>                  code <file.bin>        out <dir>
//   region <id> <bytes> [init <file>]   (a device-address buffer; its address is real)
//   poke <id> <byteOffset> <u32>        ptrlo <id> <byteOffset> <targetId> [addBytes]
//   ptrhi <id> <byteOffset> <targetId> <orMask>     ((address >> 32) & 0xffff) | orMask
//   user <sgpr> <targetId>              (user data pair: low, high dword of the region address)
//   userval <sgpr> <u32>
// Supported bindings: guest buffers (V# bases must be region starts), shader data, flattened SRT, GDS/page-table/fault stubs.
// Images and samplers are not implemented yet and make the run fail with a clear message.
#include "Recompiler.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace ShaderRecompiler;

namespace {

#define VK(call) do { const VkResult r_ = (call); if (r_ != VK_SUCCESS) throw std::runtime_error(std::string(#call) + " failed: " + std::to_string(r_)); } while (0)

struct Region {
    std::uint32_t id = 0;
    std::uint64_t size = 0;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void* mapped = nullptr;
    std::uint64_t address = 0;
};

struct Device {
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    std::uint32_t family = 0;
    VkPhysicalDeviceMemoryProperties memory{};
    PFN_vkGetBufferDeviceAddress getAddress = nullptr;
};

std::uint32_t FindMemory(const Device& d, std::uint32_t bits, VkMemoryPropertyFlags flags) {
    for (std::uint32_t i = 0; i < d.memory.memoryTypeCount; ++i) {
        if ((bits & (1u << i)) && (d.memory.memoryTypes[i].propertyFlags & flags) == flags) return i;
    }
    throw std::runtime_error("no suitable memory type");
}

void MakeBuffer(Device& d, Region& r, std::uint64_t size, VkBufferUsageFlags usage) {
    r.size = size;
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = size;
    bi.usage = usage | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    VK(vkCreateBuffer(d.device, &bi, nullptr, &r.buffer));
    VkMemoryRequirements req{};
    vkGetBufferMemoryRequirements(d.device, r.buffer, &req);
    VkMemoryAllocateFlagsInfo flags{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO};
    flags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &flags};
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = FindMemory(d, req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    VK(vkAllocateMemory(d.device, &ai, nullptr, &r.memory));
    VK(vkBindBufferMemory(d.device, r.buffer, r.memory, 0));
    VK(vkMapMemory(d.device, r.memory, 0, VK_WHOLE_SIZE, 0, &r.mapped));
    std::memset(r.mapped, 0, size);
    VkBufferDeviceAddressInfo info{VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO};
    info.buffer = r.buffer;
    r.address = d.getAddress(d.device, &info);
}

Device CreateDevice(std::uint32_t wave) {
    Device d;
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.apiVersion = VK_API_VERSION_1_3;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    VK(vkCreateInstance(&ici, nullptr, &d.instance));
    std::uint32_t count = 0;
    vkEnumeratePhysicalDevices(d.instance, &count, nullptr);
    std::vector<VkPhysicalDevice> devices(count);
    vkEnumeratePhysicalDevices(d.instance, &count, devices.data());
    for (auto candidate : devices) {
        VkPhysicalDeviceProperties props{};
        vkGetPhysicalDeviceProperties(candidate, &props);
        if (props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU || d.physical == VK_NULL_HANDLE) d.physical = candidate;
    }
    if (d.physical == VK_NULL_HANDLE) throw std::runtime_error("no Vulkan device");
    vkGetPhysicalDeviceMemoryProperties(d.physical, &d.memory);
    std::uint32_t qc = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(d.physical, &qc, nullptr);
    std::vector<VkQueueFamilyProperties> qf(qc);
    vkGetPhysicalDeviceQueueFamilyProperties(d.physical, &qc, qf.data());
    for (std::uint32_t i = 0; i < qc; ++i) if (qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { d.family = i; break; }
    // Enable every core 1.1 - 1.3 feature the device has (the recompiler asks for many of them).
    VkPhysicalDeviceVulkan13Features f13{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &f13};
    VkPhysicalDeviceVulkan11Features f11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, &f12};
    VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &f11};
    vkGetPhysicalDeviceFeatures2(d.physical, &f2);
    const float priority = 1.0f;
    VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qi.queueFamilyIndex = d.family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &priority;
    VkDeviceCreateInfo dci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &f2};
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qi;
    VK(vkCreateDevice(d.physical, &dci, nullptr, &d.device));
    vkGetDeviceQueue(d.device, d.family, 0, &d.queue);
    d.getAddress = reinterpret_cast<PFN_vkGetBufferDeviceAddress>(vkGetDeviceProcAddr(d.device, "vkGetBufferDeviceAddress"));
    (void)wave;
    return d;
}

std::vector<char> ReadFile(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot read " + path);
    return std::vector<char>((std::istreambuf_iterator<char>(f)), {});
}

std::uint64_t Num(const std::string& s) { return std::stoull(s, nullptr, 0); }

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: exec <scenario.txt>\n"); return 2; }
    try {
        std::uint32_t wave = 32;
        std::uint32_t groups[3] = {1, 1, 1};
        std::string codePath, outDir = ".";
        struct Line { std::vector<std::string> w; };
        std::vector<Line> lines;
        {
            std::ifstream sf(argv[1]);
            std::string text;
            while (std::getline(sf, text)) {
                std::istringstream ss(text);
                Line l; std::string tok;
                while (ss >> tok) l.w.push_back(tok);
                if (!l.w.empty() && l.w[0][0] != '#') lines.push_back(std::move(l));
            }
        }
        for (auto& l : lines) {
            if (l.w[0] == "wave") wave = static_cast<std::uint32_t>(Num(l.w[1]));
            else if (l.w[0] == "groups") for (int i = 0; i < 3; ++i) groups[i] = static_cast<std::uint32_t>(Num(l.w[1 + i]));
            else if (l.w[0] == "code") codePath = l.w[1];
            else if (l.w[0] == "out") outDir = l.w[1];
        }
        Device dev = CreateDevice(wave);
        std::map<std::uint32_t, Region> regions;
        for (auto& l : lines) {
            if (l.w[0] != "region") continue;
            Region& r = regions[static_cast<std::uint32_t>(Num(l.w[1]))];
            r.id = static_cast<std::uint32_t>(Num(l.w[1]));
            MakeBuffer(dev, r, Num(l.w[2]), 0);
            if (l.w.size() >= 5 && l.w[3] == "init") {
                const auto data = ReadFile(l.w[4]);
                std::memcpy(r.mapped, data.data(), std::min<std::size_t>(data.size(), r.size));
            }
        }
        const auto region = [&](const std::string& id) -> Region& {
            const auto it = regions.find(static_cast<std::uint32_t>(Num(id)));
            if (it == regions.end()) throw std::runtime_error("unknown region " + id);
            return it->second;
        };
        const auto dword = [&](Region& r, std::uint64_t off) -> std::uint32_t& {
            if (off + 4 > r.size) throw std::runtime_error("patch outside region");
            return *reinterpret_cast<std::uint32_t*>(static_cast<std::byte*>(r.mapped) + off);
        };
        std::vector<std::uint32_t> userData(16, 0);
        for (auto& l : lines) {
            if (l.w[0] == "poke") dword(region(l.w[1]), Num(l.w[2])) = static_cast<std::uint32_t>(Num(l.w[3]));
            else if (l.w[0] == "ptrlo") dword(region(l.w[1]), Num(l.w[2])) = static_cast<std::uint32_t>(region(l.w[3]).address + (l.w.size() > 4 ? Num(l.w[4]) : 0));
            else if (l.w[0] == "ptrhi") dword(region(l.w[1]), Num(l.w[2])) = static_cast<std::uint32_t>(((region(l.w[3]).address >> 32) & 0xffffu) | Num(l.w[4]));
            else if (l.w[0] == "user") { const auto k = Num(l.w[1]); userData.at(k) = static_cast<std::uint32_t>(region(l.w[2]).address); userData.at(k + 1) = static_cast<std::uint32_t>(region(l.w[2]).address >> 32); }
            else if (l.w[0] == "userval") userData.at(Num(l.w[1])) = static_cast<std::uint32_t>(Num(l.w[2]));
        }

        // Compile against a snapshot of the real contents at the real addresses.
        const auto raw = ReadFile(codePath);
        std::vector<std::uint32_t> code(raw.size() / 4);
        std::memcpy(code.data(), raw.data(), code.size() * 4);
        std::vector<std::vector<std::byte>> snapshots;
        std::vector<MemoryRegion> memory;
        for (auto& [id, r] : regions) {
            snapshots.emplace_back(static_cast<std::byte*>(r.mapped), static_cast<std::byte*>(r.mapped) + r.size);
            memory.push_back({r.address, std::span<const std::byte>(snapshots.back())});
        }
        RecompileRequest request{};
        request.context.memory = MemoryRegionView(std::span<const MemoryRegion>(memory));
        const std::vector<std::uint32_t> caps{1u,55u,56u,32u,33u,5283u,9u,10u,11u,22u,39u,61u,62u,63u,64u,65u,66u,67u,68u,4427u,4433u,4434u,4435u,4436u,4437u,4438u,4445u,4447u,4448u,4449u,5301u,5302u,5303u};
        const std::array<std::string_view, 6> ext{"SPV_KHR_storage_buffer_storage_class", "SPV_KHR_physical_storage_buffer", "SPV_KHR_16bit_storage", "SPV_KHR_float_controls", "SPV_KHR_8bit_storage", "SPV_KHR_shader_ballot"};
        request.shader = {ShaderStage::Compute, 0x20000u, code};
        request.context.waveSize = wave;
        request.context.userDataBaseRegister = 0;
        request.context.userData = userData;
        request.context.compute = ShaderComputeStageInfo{{wave, 1u, 1u}, 0u, {true, true, true}, false, 1u};
        request.target.vulkanVersion = 0x00403000u;
        request.target.spirvVersion = 0x00010600u;
        request.target.subgroupSize = wave;
        request.target.bdaAbiVersion = 1;
        request.target.supportedCapabilities = caps;
        request.target.supportedExtensions = std::vector<std::string_view>(ext.begin(), ext.end());
        request.target.maxWorkgroupSize = {1024u, 1024u, 64u};
        request.target.maxWorkgroupInvocations = 1024;
        request.target.maxWorkgroupSharedMemoryBytes = 65536;
        request.layout = {0, 0, 0, 128};
        request.useCache = false;
        const auto result = Recompile(request);
        if (result.spirv.empty()) throw std::runtime_error("recompile produced no module");
        std::printf("compiled: %zu words, %zu bindings, push %zu bytes, bdaAbi %u\n", result.spirv.Words().size(), result.bindings.size(), result.pushConstants.size(), result.bdaAbiVersion);

        // Descriptor layout + resources for each binding.
        std::vector<VkDescriptorSetLayoutBinding> layoutBindings;
        std::vector<std::vector<VkDescriptorBufferInfo>> infos(result.bindings.size());
        std::vector<Region> extra;
        extra.reserve(result.bindings.size() * 4 + 8);
        const auto regionAt = [&](std::uint64_t addr) -> Region* {
            for (auto& [id, r] : regions) if (addr >= r.address && addr < r.address + r.size) return &r;
            return nullptr;
        };
        for (std::size_t bi = 0; bi < result.bindings.size(); ++bi) {
            const auto& b = result.bindings[bi];
            if (b.descriptorSet != 0) throw std::runtime_error("descriptor sets other than 0 are not implemented");
            if (b.kind != DescriptorKind::StorageBuffer) throw std::runtime_error("binding " + std::to_string(b.binding) + ": images/samplers/texel buffers are not implemented in the harness");
            layoutBindings.push_back({b.binding, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, b.count, VK_SHADER_STAGE_COMPUTE_BIT, nullptr});
            for (std::uint32_t e = 0; e < b.count; ++e) {
                VkDescriptorBufferInfo info{VK_NULL_HANDLE, 0, VK_WHOLE_SIZE};
                if (b.role == DescriptorRole::GuestBuffers) {
                    const std::uint64_t base = b.guestDescriptor[e * 4] | (static_cast<std::uint64_t>(b.guestDescriptor[e * 4 + 1] & 0xffffu) << 32);
                    Region* r = regionAt(base);
                    if (r == nullptr) throw std::runtime_error("V# base 0x" + std::to_string(base) + " lies in no region");
                    if (r->address != base) throw std::runtime_error("V# base is not a region start (unaligned element offsets are not implemented)");
                    info.buffer = r->buffer;
                } else if (b.role == DescriptorRole::ShaderData || b.role == DescriptorRole::FlattenedSrt) {
                    extra.emplace_back();
                    MakeBuffer(dev, extra.back(), std::max<std::size_t>(b.guestDescriptor.size() * 4, 16), 0);
                    std::memcpy(extra.back().mapped, b.guestDescriptor.data(), b.guestDescriptor.size() * 4);
                    info.buffer = extra.back().buffer;
                } else if (b.role == DescriptorRole::Gds || b.role == DescriptorRole::BdaPagetable || b.role == DescriptorRole::FaultBuffer) {
                    extra.emplace_back();
                    MakeBuffer(dev, extra.back(), 1u << 16, 0);
                    info.buffer = extra.back().buffer;
                } else {
                    throw std::runtime_error("binding " + std::to_string(b.binding) + ": role " + std::to_string(static_cast<int>(b.role)) + " is not implemented in the harness");
                }
                infos[bi].push_back(info);
            }
        }
        VkDescriptorSetLayoutCreateInfo dsli{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        dsli.bindingCount = static_cast<std::uint32_t>(layoutBindings.size());
        dsli.pBindings = layoutBindings.data();
        VkDescriptorSetLayout setLayout;
        VK(vkCreateDescriptorSetLayout(dev.device, &dsli, nullptr, &setLayout));
        VkPushConstantRange pcr{VK_SHADER_STAGE_COMPUTE_BIT, 0, static_cast<std::uint32_t>(std::max<std::size_t>(result.pushConstants.size(), 128))};
        VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        pli.setLayoutCount = 1; pli.pSetLayouts = &setLayout; pli.pushConstantRangeCount = 1; pli.pPushConstantRanges = &pcr;
        VkPipelineLayout pipelineLayout;
        VK(vkCreatePipelineLayout(dev.device, &pli, nullptr, &pipelineLayout));

        const auto words = result.spirv.Words();
        VkShaderModuleCreateInfo smi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        smi.codeSize = words.size() * 4; smi.pCode = words.data();
        VkShaderModule module;
        VK(vkCreateShaderModule(dev.device, &smi, nullptr, &module));
        std::vector<VkSpecializationMapEntry> entries;
        std::vector<std::uint32_t> specData;
        for (const auto& c : result.specialization) { entries.push_back({c.id, static_cast<std::uint32_t>(specData.size() * 4), 4}); specData.push_back(c.value); }
        VkSpecializationInfo spec{static_cast<std::uint32_t>(entries.size()), entries.data(), specData.size() * 4, specData.data()};
        VkPipelineShaderStageRequiredSubgroupSizeCreateInfo sub{VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO};
        sub.requiredSubgroupSize = result.hostSubgroupSize != 0 ? result.hostSubgroupSize : wave;
        VkComputePipelineCreateInfo cpi{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        cpi.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, &sub, result.fullSubgroups ? VK_PIPELINE_SHADER_STAGE_CREATE_REQUIRE_FULL_SUBGROUPS_BIT : 0u, VK_SHADER_STAGE_COMPUTE_BIT, module, "main", entries.empty() ? nullptr : &spec};
        cpi.layout = pipelineLayout;
        VkPipeline pipeline;
        VK(vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &cpi, nullptr, &pipeline));

        VkDescriptorPoolSize ps{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 4096};
        VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        dpi.maxSets = 1; dpi.poolSizeCount = 1; dpi.pPoolSizes = &ps;
        VkDescriptorPool pool;
        VK(vkCreateDescriptorPool(dev.device, &dpi, nullptr, &pool));
        VkDescriptorSetAllocateInfo dsai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        dsai.descriptorPool = pool; dsai.descriptorSetCount = 1; dsai.pSetLayouts = &setLayout;
        VkDescriptorSet set;
        VK(vkAllocateDescriptorSets(dev.device, &dsai, &set));
        std::vector<VkWriteDescriptorSet> writes;
        for (std::size_t bi = 0; bi < result.bindings.size(); ++bi) {
            VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
            w.dstSet = set; w.dstBinding = result.bindings[bi].binding; w.descriptorCount = result.bindings[bi].count;
            w.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; w.pBufferInfo = infos[bi].data();
            writes.push_back(w);
        }
        vkUpdateDescriptorSets(dev.device, static_cast<std::uint32_t>(writes.size()), writes.data(), 0, nullptr);

        VkCommandPoolCreateInfo cpci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        cpci.queueFamilyIndex = dev.family;
        VkCommandPool cmdPool;
        VK(vkCreateCommandPool(dev.device, &cpci, nullptr, &cmdPool));
        VkCommandBufferAllocateInfo cbai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        cbai.commandPool = cmdPool; cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; cbai.commandBufferCount = 1;
        VkCommandBuffer cmd;
        VK(vkAllocateCommandBuffers(dev.device, &cbai, &cmd));
        VkCommandBufferBeginInfo bbi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        VK(vkBeginCommandBuffer(cmd, &bbi));
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout, 0, 1, &set, 0, nullptr);
        if (!result.pushConstants.empty()) vkCmdPushConstants(cmd, pipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, static_cast<std::uint32_t>(result.pushConstants.size()), result.pushConstants.data());
        vkCmdDispatch(cmd, groups[0], groups[1], groups[2]);
        VK(vkEndCommandBuffer(cmd));
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
        VK(vkQueueSubmit(dev.queue, 1, &si, VK_NULL_HANDLE));
        VK(vkQueueWaitIdle(dev.queue));

        std::filesystem::create_directories(outDir);
        for (auto& [id, r] : regions) {
            std::ofstream o(outDir + "/region" + std::to_string(id) + ".bin", std::ios::binary);
            o.write(static_cast<const char*>(r.mapped), static_cast<std::streamsize>(r.size));
        }
        std::printf("RAN\n");
        return 0;
    } catch (const std::exception& e) {
        std::string m = e.what();
        for (auto& c : m) if (c == '\n' || c == '\r') c = ' ';
        std::printf("FAIL %s\n", m.c_str());
        return 1;
    }
}
