#pragma once

#include <cstdio>
#include <string>

// Serialization of self-reported facts only. Kept independent of Android and
// Vulkan so the exact production writer can be checked on the host.
namespace refbench_output {

inline std::string quote(const std::string &value) {
    std::string out = "\"";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') {
            out += '\\';
            out += char(c);
        } else if (c < 0x20) {
            char escaped[7];
            snprintf(escaped, sizeof escaped, "\\u%04x", unsigned(c));
            out += escaped;
        } else {
            out += char(c);
        }
    }
    return out + '"';
}

inline const char *boolean(bool value) { return value ? "true" : "false"; }

template<class Config>
auto bandwidth_capture(const Config& cfg, int) -> decltype(cfg.capture_bw_hash == "on", bool()) {
    return cfg.capture_bw_hash == "on";
}
template<class Config> bool bandwidth_capture(const Config&, long) { return false; }

inline const char *declared_bottleneck(const std::string &scene) {
    if (scene == "bw_pingpong") return "bandwidth";
    if (scene == "frag_alu") return "fragment";
    if (scene == "sr_pipeline") return "fragment+compute";
    return "none";
}

// State supplies the existing cfg, pass declarations, and bookkeeping fields.
// Only enum-to-name conversion remains with the Vulkan caller. This function
// does not derive device/performance eligibility from a successful JSON write.
template <class State>
std::string serialize(const State &state, const std::string &app_version,
                      const std::string &render_thread_name,
                      const std::string &driver_thread_name,
                      const std::string &present_mode,
                      const std::string &swap_format) {
    const auto &cfg = state.cfg;
    std::string j;
    j.reserve(4096);
    j += "{\n \"contract_version\": 1,\n \"app_version\": " + quote(app_version);
    j += ",\n \"run_id\": " + quote(cfg.run_id);
    j += ",\n \"scene\": " + quote(cfg.scene);
    j += ",\n \"declared_bottleneck\": " + quote(declared_bottleneck(cfg.scene));
    j += ",\n \"effective_knobs\": {";
    if (cfg.scene == "bw_pingpong") j += "\"loadop\": " + quote(cfg.knob_loadop);
    if (cfg.scene == "sr_pipeline") j += "\"postfx\": " + quote(cfg.knob_postfx);
    j += "},\n";
    if (cfg.capture_native_pairs == "on" || bandwidth_capture(cfg, 0))
        j += " \"capture_mode\": \"quality_only\",\n \"performance_eligible\": false,\n";
    if (cfg.scene == "sr_pipeline") {
        j += " \"postfx\": {\"active\": " + std::string(boolean(state.postfx_active));
        j += ", \"shader_path\": " + quote(cfg.postfx_shader);
        j += ", \"shader_bytes\": " + std::to_string(state.postfx_bytes);
        j += ", \"lo\": [" + std::to_string(State::SR_LO_W) + ", " + std::to_string(State::SR_LO_H);
        j += "], \"hi\": [" + std::to_string(State::SR_HI_W) + ", " + std::to_string(State::SR_HI_H);
        j += "], \"workgroup\": [16, 16, 1]},\n";
    }
    const auto rt_width = cfg.scene == "frag_alu" ? State::ALU_RT :
        cfg.scene == "bw_pingpong" ? State::BW_RT : cfg.scene == "sr_pipeline" ? State::SR_LO_W : 0;
    const auto rt_height = cfg.scene == "frag_alu" ? State::ALU_RT :
        cfg.scene == "bw_pingpong" ? State::BW_RT : cfg.scene == "sr_pipeline" ? State::SR_LO_H : 0;
    j += " \"params\": {\"frames\": " + std::to_string(cfg.frames);
    j += ", \"intensity\": " + std::to_string(cfg.intensity);
    j += ", \"rt_width\": " + std::to_string(rt_width) + ", \"rt_height\": " + std::to_string(rt_height) + "},\n";
    j += " \"render_thread_name\": " + quote(render_thread_name);
    j += ",\n \"driver_submit_thread_name\": " + quote(driver_thread_name);
    j += ",\n \"renamed_driver_threads\": " + std::to_string(state.renamed_driver_threads);
    j += ",\n \"gpu_name\": " + quote(state.gpu_name);
    j += ",\n \"swapchain\": {\"present_mode\": " + quote(present_mode) + ", \"format\": " + quote(swap_format);
    j += ", \"width\": " + std::to_string(state.swap_extent.width) + ", \"height\": " + std::to_string(state.swap_extent.height);
    j += ", \"image_count\": " + std::to_string(state.swap_images.size());
    char transform[11];
    snprintf(transform, sizeof transform, "0x%x", unsigned(state.pre_transform));
    j += ", \"pre_transform\": " + quote(transform) + "},\n \"passes\": [\n";
    for (size_t i = 0; i < state.passes.size(); ++i) {
        const auto &pass = state.passes[i];
        j += "  {\"name\": " + quote(pass.name) + ", \"target\": " + quote(pass.target);
        j += ", \"load_op\": " + quote(pass.load_op) + ", \"store_op\": " + quote(pass.store_op);
        j += ", \"width\": " + std::to_string(pass.w) + ", \"height\": " + std::to_string(pass.h);
        j += ", \"format\": " + quote(pass.format) + "}";
        j += i + 1 < state.passes.size() ? ",\n" : "\n";
    }
    j += " ],\n \"debug_labels_supported\": " + std::string(boolean(state.debug_labels));
    j += ",\n \"frames_submitted\": " + std::to_string(state.frames_submitted);
    j += ",\n \"clean_exit\": " + std::string(boolean(state.clean_exit)) + "\n}\n";
    return j;
}

} // namespace refbench_output
