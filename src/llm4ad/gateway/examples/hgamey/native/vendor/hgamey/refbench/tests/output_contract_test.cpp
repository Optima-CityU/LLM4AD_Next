#include "output_json.h"
#include <cstdint>
#include <iostream>
#include <vector>

// CPU-only declared facts, never a Vulkan run or device qualification. The
// template instantiates the same writer used by main.cpp with this host state.
struct Config {
    std::string scene = "bw_pingpong", run_id = "host_fixture";
    int frames = 60, intensity = 8;
    std::string knob_loadop = "off", knob_postfx = "off";
    std::string capture_native_pairs = "off", postfx_shader;
};
struct Pass {
    std::string name, target, load_op, store_op, format;
    uint32_t w, h;
};
struct HostState {
    Config cfg;
    static constexpr uint32_t BW_RT = 2048, ALU_RT = 1024;
    static constexpr uint32_t SR_LO_W = 1280, SR_LO_H = 720;
    static constexpr uint32_t SR_HI_W = 1920, SR_HI_H = 1080;
    std::string gpu_name = "host fixture; no GPU execution";
    struct { uint32_t width = 1080, height = 2400; } swap_extent;
    std::vector<int> swap_images = {0, 1, 2};
    unsigned pre_transform = 1;
    std::vector<Pass> passes;
    long postfx_bytes = 0;
    bool postfx_active = false, debug_labels = false, clean_exit = true;
    int frames_submitted = 60, renamed_driver_threads = 0;
};

int main() {
    bool first = true;
    std::cout << '[';
    auto emit = [&](const char *name, const HostState &state) {
        if (!first) std::cout << ',';
        first = false;
        std::cout << "{\"case\":" << refbench_output::quote(name) << ",\"output\":"
                  << refbench_output::serialize(state, "0.1.0", "RefbenchRender", "RefbenchDrv",
                                               "FIFO", "R8G8B8A8_UNORM") << '}';
    };
    HostState state;
    state.passes = {{"ping_0", "rtB", "LOAD", "STORE", "R16G16B16A16_SFLOAT", 2048, 2048}};
    emit("bw_off", state);
    state.cfg.knob_loadop = "on";
    state.passes[0].load_op = "DONT_CARE";
    emit("bw_on", state);
    state.cfg.scene = "frag_alu";
    state.passes = {{"alu_main", "rtC", "DONT_CARE", "STORE", "R8G8B8A8_UNORM", 1024, 1024}};
    emit("fragment", state);
    state.cfg.scene = "idle_cap";
    state.passes = {{"idle_tri", "swapchain", "CLEAR", "STORE", "R8G8B8A8_UNORM", 1080, 2400}};
    emit("idle", state);
    state.cfg.scene = "sr_pipeline";
    state.passes = {{"scene_lowres", "rtLo", "DONT_CARE", "STORE", "R8G8B8A8_UNORM", 1280, 720}};
    emit("sr_off", state);
    state.cfg.knob_postfx = "on";
    state.cfg.postfx_shader = "/data/local/tmp/fixture.spv";
    state.postfx_active = true;
    state.postfx_bytes = 1024;
    state.passes.push_back({"postfx_compute", "rtHi", "NONE", "STORE", "R8G8B8A8_UNORM", 1920, 1080});
    emit("sr_on", state);
    state.cfg.capture_native_pairs = "on";
    state.passes.push_back({"scene_native_hr_capture_only", "nativeHR", "DONT_CARE", "STORE", "R8G8B8A8_UNORM", 1920, 1080});
    emit("sr_capture", state);
    state.cfg.capture_native_pairs = "off";
    state.cfg.postfx_shader.clear();
    for (int i = 0; i < 5; ++i) state.cfg.postfx_shader += "/" + std::string(180, 'p');
    state.cfg.postfx_shader += "/shader\"\\\n\t.spv";
    state.cfg.run_id = std::string("run\0id", 6) + "\"\\\n";
    state.gpu_name = "host \"GPU\" \\ \t 渲染";
    state.passes[1].name = std::string(600, 'n') + "\"\\\n";
    emit("long_strings", state);
    state.postfx_active = false;
    state.clean_exit = false;
    state.frames_submitted = 0;
    emit("sr_failed", state);
    std::cout << "]\n";
}
