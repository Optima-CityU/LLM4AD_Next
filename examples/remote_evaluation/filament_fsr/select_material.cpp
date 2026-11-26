#include <filament/Material.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <vector>

using Builder = filament::Material::Builder;

extern "C" const std::uint8_t fsr_original[], fsr_original_end[];

Builder& real_package(Builder*, const void*, std::size_t)
        asm("__real__ZN8filament8Material7Builder7packageEPKvm");
Builder& benchmark_package(Builder*, const void*, std::size_t)
        asm("__wrap__ZN8filament8Material7Builder7packageEPKvm");

namespace {
std::vector<char> candidate;
unsigned int easu_packages = 0;
}

void load_candidate_material(const char* path) {
    std::ifstream stream(path, std::ios::binary);
    candidate.assign(std::istreambuf_iterator<char>(stream), {});
    if (candidate.empty()) {
        throw std::runtime_error("Could not read the candidate Filament material");
    }
}

void verify_easu_material(bool upscaling) {
    if (easu_packages != (upscaling ? 1u : 0u)) {
        throw std::runtime_error("Unexpected mobile EASU material load count");
    }
}

Builder& benchmark_package(Builder* builder, const void* data, std::size_t size) {
    const auto stock_size = reinterpret_cast<std::uintptr_t>(fsr_original_end)
            - reinterpret_cast<std::uintptr_t>(fsr_original);
    if (size != stock_size || std::memcmp(data, fsr_original, size) != 0) {
        return real_package(builder, data, size);
    }
    ++easu_packages;
    std::puts(candidate.empty() ? "EASU_PACKAGE stock" : "EASU_PACKAGE candidate");
    if (candidate.empty()) {
        return real_package(builder, data, size);
    }
    return real_package(builder, candidate.data(), candidate.size());
}
