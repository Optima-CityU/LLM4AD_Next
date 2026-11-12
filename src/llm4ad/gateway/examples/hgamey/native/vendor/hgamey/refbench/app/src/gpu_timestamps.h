#pragma once

#include "output_json.h"
#include <array>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

// Optional raw Vulkan-query evidence. No rates, statistics or verdicts here;
// phonefarm owns conversion and interpretation. Content stays frame-index based.
namespace refbench_timestamps {
class Capture {
    std::vector<std::array<uint64_t, 2>> values_;
    std::vector<bool> ready_;
    unsigned bits_ = 0;
    double period_ = 0;
    size_t count_ = 0;
public:
    bool configure(size_t frames, unsigned bits, double period) {
        if (!frames || frames > 1000000 || !bits || bits > 64 ||
                !std::isfinite(period) || period <= 0) return false;
        values_.assign(frames, {0, 0});
        ready_.assign(frames, false);
        bits_ = bits;
        period_ = period;
        count_ = 0;
        return true;
    }
    bool record(size_t frame, uint64_t start, uint64_t end) {
        if (frame >= values_.size() || ready_[frame]) return false;
        values_[frame] = {start, end};
        ready_[frame] = true;
        ++count_;
        return true;
    }
    bool complete() const { return count_ && count_ == values_.size(); }
    std::string json(const std::string& run_id) const {
        std::string j = "{\"schema\":\"refbench/gpu-timestamps@1\",\"run_id\":" +
            refbench_output::quote(run_id) + ",\"timestamp_valid_bits\":" + std::to_string(bits_) +
            ",\"timestamp_period_ns\":" + std::to_string(period_) +
            ",\"frames_config\":" + std::to_string(values_.size()) +
            ",\"complete\":" + (complete() ? "true" : "false") + ",\"samples\":[";
        bool first = true;
        for (size_t i = 0; i < values_.size(); ++i) {
            if (!ready_[i]) continue;
            if (!first) j += ',';
            first = false;
            j += '[' + std::to_string(i) + ',' + std::to_string(values_[i][0]) + ',' +
                std::to_string(values_[i][1]) + ']';
        }
        return j + "]}";
    }
};
}
