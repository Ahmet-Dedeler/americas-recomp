#include "ControlFlow/GraphBuilder.hpp"
#include "ControlFlow/Structurizer.hpp"
#include "RdnaDecoder/RdnaInstructionDecoder.hpp"
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <vector>
using namespace ShaderRecompiler;
using C = std::chrono::steady_clock;
static double ms(C::time_point a) { return std::chrono::duration<double, std::milli>(C::now() - a).count(); }
int main(int argc, char** argv) {
    std::ifstream f(argv[1], std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(f)), {});
    std::vector<std::uint32_t> code(raw.size() / 4);
    std::memcpy(code.data(), raw.data(), code.size() * 4);
    auto t = C::now();
    const auto decoded = RdnaInstructionDecoder{}.Decode(code);
    std::printf("decode %.0fms\n", ms(t)); std::fflush(stdout); t = C::now();
    auto graph = GraphBuilder{}.Build(decoded);
    std::printf("build %.0fms blocks=%zu\n", ms(t), graph.blocks.size()); std::fflush(stdout); t = C::now();
    Structurizer{}.Structurize(graph);
    std::printf("structurize %.0fms blocks=%zu\n", ms(t), graph.blocks.size());
}
