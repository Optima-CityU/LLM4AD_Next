#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

// Caller holds the layer mutex. These counts describe recording / accepted submission,
// never GPU completion. Independent of Vulkan headers so host tests exercise the same logic.
struct ArtifactStats {
    uint64_t pipeline_created = 0, dispatch_recorded = 0, queue_submitted = 0;
    uint64_t substituted = 0, disabled = 0;
    std::string disabled_reason;
    std::map<uintptr_t, uint64_t> pending;
    std::map<uintptr_t, uintptr_t> pools;

    void allocated(uintptr_t cb, uintptr_t pool) { pending.erase(cb); pools[cb] = pool; }
    void record(uintptr_t cb) { ++dispatch_recorded; ++pending[cb]; }
    void reset(uintptr_t cb) { pending.erase(cb); }
    void free(uintptr_t cb) { reset(cb); pools.erase(cb); }
    void reset_pool(uintptr_t pool, bool destroy = false) {
        for (auto it = pools.begin(); it != pools.end();) {
            if (it->second != pool) { ++it; continue; }
            pending.erase(it->first);
            if (destroy) it = pools.erase(it); else ++it;
        }
    }
    void inherit(uintptr_t primary, uintptr_t secondary) {
        auto it = pending.find(secondary);
        if (it != pending.end()) pending[primary] += it->second;
    }
    uint64_t submitted_count(const std::vector<uintptr_t>& cbs) const {
        uint64_t count = 0;
        for (auto cb : cbs) { auto it = pending.find(cb); if (it != pending.end()) count += it->second; }
        return count;
    }
    void submit_result(uint64_t count, bool success) { if (success) queue_submitted += count; }
    void disable(const char* reason) {
        if (disabled_reason.empty()) { ++disabled; disabled_reason = reason; }
    }
    void clear_pending() { pending.clear(); pools.clear(); }
    std::string json() const {
        // Reasons are internal fixed strings / Vulkan operation names, not caller text.
        return "{\"pipeline_created\":" + std::to_string(pipeline_created)
            + ",\"dispatch_recorded\":" + std::to_string(dispatch_recorded)
            + ",\"queue_submitted\":" + std::to_string(queue_submitted)
            + ",\"substituted\":" + std::to_string(substituted)
            + ",\"disabled\":" + std::to_string(disabled)
            + ",\"disabled_reason\":" + (disabled_reason.empty() ? "null" : "\"" + disabled_reason + "\"") + "}";
    }
};
