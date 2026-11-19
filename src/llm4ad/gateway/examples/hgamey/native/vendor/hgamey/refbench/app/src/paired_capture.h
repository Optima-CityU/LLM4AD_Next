#pragma once
// Capture bookkeeping and file identity only; no quality/performance
// statistics.
#include <array>
#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <iomanip>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace paired_capture {
class Sha256 {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint8_t block[64]{};
    size_t used = 0;
    uint64_t size = 0;
    static uint32_t rot(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
    void compress() {
        static constexpr uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
            0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
            0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
            0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
            0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
            0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2};
        uint32_t w[64]{};
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t(block[4 * i]) << 24) | (uint32_t(block[4 * i + 1]) << 16) |
                   (uint32_t(block[4 * i + 2]) << 8) | block[4 * i + 3];
        for (int i = 16; i < 64; i++) {
            uint32_t a = w[i - 15], b = w[i - 2];
            w[i] = w[i - 16] + (rot(a, 7) ^ rot(a, 18) ^ (a >> 3)) + w[i - 7] +
                   (rot(b, 17) ^ rot(b, 19) ^ (b >> 10));
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], v = h[7];
        for (int i = 0; i < 64; i++) {
            uint32_t t =
                v + (rot(e, 6) ^ rot(e, 11) ^ rot(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            uint32_t u = (rot(a, 2) ^ rot(a, 13) ^ rot(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
            v = g;
            g = f;
            f = e;
            e = d + t;
            d = c;
            c = b;
            b = a;
            a = t + u;
        }
        h[0] += a;
        h[1] += b;
        h[2] += c;
        h[3] += d;
        h[4] += e;
        h[5] += f;
        h[6] += g;
        h[7] += v;
    }

  public:
    void update(const void *input, size_t n) {
        const uint8_t *p = static_cast<const uint8_t *>(input);
        size += n;
        while (n) {
            size_t take = 64 - used < n ? 64 - used : n;
            memcpy(block + used, p, take);
            used += take;
            p += take;
            n -= take;
            if (used == 64) {
                compress();
                used = 0;
            }
        }
    }
    std::string finish() {
        uint64_t bits = size * 8;
        uint8_t one = 0x80, zero = 0;
        update(&one, 1);
        while (used != 56)
            update(&zero, 1);
        uint8_t length[8];
        for (int i = 0; i < 8; i++)
            length[7 - i] = uint8_t(bits >> (i * 8));
        update(length, 8);
        std::ostringstream out;
        out << std::hex << std::setfill('0');
        for (uint32_t x : h)
            out << std::setw(8) << x;
        return out.str();
    }
};
inline std::string digest(const void *bytes, size_t size) {
    Sha256 s;
    s.update(bytes, size);
    return s.finish();
}
inline bool file_hash(const std::string &path, std::string *hash, std::string *contents = nullptr) {
    FILE *f = fopen(path.c_str(), "rb");
    if (!f)
        return false;
    Sha256 s;
    char b[65536];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f))) {
        s.update(b, n);
        if (contents) {
            if (contents->size() + n > 1024 * 1024) {
                fclose(f);
                return false;
            }
            contents->append(b, n);
        }
    }
    bool ok = !ferror(f);
    fclose(f);
    if (ok)
        *hash = s.finish();
    return ok;
}
inline bool hash_ok(const std::string &s) {
    return s.size() == 64 && s.find_first_not_of("0123456789abcdef") == std::string::npos;
}
inline bool name_ok(const std::string &s) {
    return !s.empty() && s.size() <= 128 && s != "." && s != ".." && s != "unset" &&
           s.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVW"
                               "XYZ0123456789_.-") == std::string::npos;
}
inline uint64_t clock_ns(clockid_t clock) {
    timespec t{};
    if (clock_gettime(clock, &t) != 0)
        return 0;
    return uint64_t(t.tv_sec) * 1000000000ull + uint64_t(t.tv_nsec);
}
inline float phase(int frame, int total) {
    return float(2.0 * 3.14159265358979323846 * double(frame) / double(total));
}
inline uint32_t phase_bits(float value) {
    uint32_t bits;
    memcpy(&bits, &value, 4);
    return bits;
}
inline std::string quote(const std::string &value) {
    std::string out = "\"";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') {
            out += '\\';
            out += char(c);
        } else if (c < 32) {
            char b[8];
            snprintf(b, sizeof b, "\\u%04x", c);
            out += b;
        } else
            out += char(c);
    }
    return out + '"';
}

// Strict bounded JSON traversal for the one top-level metadata field. Nested
// content is parsed, duplicate top-level keys rejected; never regex JSON.
class FrozenTimeParser {
    const std::string &s;
    size_t p = 0;
    bool found = false;
    uint64_t frozen = 0;
    void ws() {
        while (p < s.size() && (s[p] == ' ' || s[p] == '\n' || s[p] == '\r' || s[p] == '\t'))
            p++;
    }
    bool take(char c) {
        ws();
        if (p < s.size() && s[p] == c) {
            p++;
            return true;
        }
        return false;
    }
    bool str(std::string *out = nullptr) {
        ws();
        if (p >= s.size() || s[p++] != '"')
            return false;
        while (p < s.size()) {
            unsigned char c = s[p++];
            if (c == '"')
                return true;
            if (c < 32)
                return false;
            if (c == '\\') {
                if (p >= s.size())
                    return false;
                char e = s[p++];
                if (e == 'u') {
                    for (int i = 0; i < 4; i++) {
                        if (p >= s.size() || !std::isxdigit(static_cast<unsigned char>(s[p++])))
                            return false;
                    }
                    if (out)
                        return false;
                } else if (std::string("\"\\/bfnrt").find(e) == std::string::npos)
                    return false;
                else if (out)
                    out->push_back(e);
            } else if (out)
                out->push_back(char(c));
        }
        return false;
    }
    bool number(uint64_t *integer = nullptr) {
        ws();
        size_t begin = p;
        if (p < s.size() && s[p] == '-') {
            if (integer)
                return false;
            p++;
        }
        if (p >= s.size())
            return false;
        if (s[p] == '0')
            p++;
        else {
            if (s[p] < '1' || s[p] > '9')
                return false;
            while (p < s.size() && s[p] >= '0' && s[p] <= '9')
                p++;
        }
        bool noninteger = false;
        if (p < s.size() && s[p] == '.') {
            noninteger = true;
            p++;
            size_t b = p;
            while (p < s.size() && s[p] >= '0' && s[p] <= '9')
                p++;
            if (b == p)
                return false;
        }
        if (p < s.size() && (s[p] == 'e' || s[p] == 'E')) {
            noninteger = true;
            p++;
            if (p < s.size() && (s[p] == '+' || s[p] == '-'))
                p++;
            size_t b = p;
            while (p < s.size() && s[p] >= '0' && s[p] <= '9')
                p++;
            if (b == p)
                return false;
        }
        if (integer) {
            if (noninteger)
                return false;
            uint64_t v = 0;
            for (size_t i = begin; i < p; i++) {
                uint64_t d = uint64_t(s[i] - '0');
                if (v > (UINT64_MAX - d) / 10)
                    return false;
                v = v * 10 + d;
            }
            *integer = v;
        }
        return true;
    }
    bool value(int depth) {
        if (depth > 32)
            return false;
        ws();
        if (p >= s.size())
            return false;
        if (s[p] == '"')
            return str();
        if (s[p] == '[') {
            p++;
            if (take(']'))
                return true;
            do {
                if (!value(depth + 1))
                    return false;
            } while (take(','));
            return take(']');
        }
        if (s[p] == '{')
            return object(depth + 1, false);
        for (const char *literal : {"true", "false", "null"}) {
            size_t n = strlen(literal);
            if (s.compare(p, n, literal) == 0) {
                p += n;
                return true;
            }
        }
        return number();
    }
    bool object(int depth, bool top) {
        if (!take('{'))
            return false;
        std::vector<std::string> keys;
        if (take('}'))
            return true;
        do {
            std::string key;
            if (!str(&key) || !take(':'))
                return false;
            if (top) {
                for (const auto &old : keys)
                    if (old == key)
                        return false;
                keys.push_back(key);
            }
            if (top && key == "frozen_at_unix_ms") {
                if (found || !number(&frozen))
                    return false;
                found = true;
            } else if (!value(depth + 1))
                return false;
        } while (take(','));
        return take('}');
    }

  public:
    explicit FrozenTimeParser(const std::string &input) : s(input) {}
    bool parse(uint64_t *output) {
        if (!object(0, true))
            return false;
        ws();
        if (p != s.size() || !found || frozen == 0)
            return false;
        *output = frozen;
        return true;
    }
};

struct Config {
    std::string run_id, rules_sha256, scene_shader_sha256, vertex_shader_sha256, candidate_sha256;
    int total_frames = 60, intensity = 1, start = 0, count = 4, stride = 1;
    uint64_t frozen_at_ms = 0;
    std::string source = "native_rendered_controlled_whitebox";
};
struct Raw {
    std::string role;
    uint32_t width, height;
    const void *bytes;
};
class Sink {
    Config cfg;
    std::string dir;
    std::string failure_reason;
    std::vector<std::string> rows;
    uint64_t start_ms = 0, end_ms = 0, last_timestamp = 0;
    struct Artifact { std::string file, sha; size_t bytes; };
    std::vector<Artifact> artifacts;
    std::string last_manifest_bytes;
    bool sticky(const char *reason) {
        if (failure_reason.empty()) failure_reason = reason;
        return false;
    }
    bool file_matches(const std::string &name, size_t size, const std::string &expected) {
        int fd = open((dir + "/" + name).c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
        if (fd < 0) return false;
        struct stat st{};
        bool ok = fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_size >= 0 &&
                  uint64_t(st.st_size) == size;
        Sha256 sha; char bytes[65536]; size_t total = 0;
        while (ok) {
            ssize_t n = read(fd, bytes, sizeof bytes);
            if (n < 0 && errno == EINTR) continue;
            if (n < 0) { ok = false; break; }
            if (!n) break;
            if (size_t(n) > size - total) { ok = false; break; }
            total += size_t(n); sha.update(bytes, size_t(n));
        }
        int closed = close(fd);
        return ok && closed == 0 && total == size && sha.finish() == expected;
    }
    bool write_new(const std::string &name, const std::string &bytes) {
        if (bytes.size() > 1024 * 1024) return false;
        int fd = open((dir + "/" + name).c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        if (fd < 0) return false;
        bool ok = write_all(fd, bytes.data(), bytes.size()) && fsync(fd) == 0;
        int closed = close(fd);
        return ok && closed == 0;
    }
    bool atomic(const std::string &path, const std::string &bytes) {
        struct stat st{};
        if (lstat(dir.c_str(), &st) != 0 || !S_ISDIR(st.st_mode))
            return false;
        if (lstat(path.c_str(), &st) == 0 && !S_ISREG(st.st_mode))
            return false;
        std::string tmp = path + ".tmp";
        int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (fd < 0)
            return false;
        bool ok = write_all(fd, bytes.data(), bytes.size()) && fsync(fd) == 0;
        int closed = close(fd);
        if (!ok || closed != 0)
            return false;
        if (rename(tmp.c_str(), path.c_str()) != 0)
            return false;
        int parent = open(dir.c_str(), O_RDONLY);
        if (parent < 0)
            return false;
        ok = fsync(parent) == 0;
        if (close(parent) != 0) ok = false;
        return ok;
    }
    static bool write_all(int fd, const void *data, size_t n) {
        const char *p = static_cast<const char *>(data);
        while (n) {
            ssize_t sent = write(fd, p, n);
            if (sent < 0 && errno == EINTR)
                continue;
            if (sent <= 0)
                return false;
            p += sent;
            n -= size_t(sent);
        }
        return true;
    }

  public:
    bool begin(const std::string &base, const Config &input) {
        cfg = input;
        if (!rows.empty() || !name_ok(cfg.run_id) || !hash_ok(cfg.rules_sha256) ||
            !hash_ok(cfg.scene_shader_sha256) || !hash_ok(cfg.vertex_shader_sha256) ||
            (!cfg.candidate_sha256.empty() && !hash_ok(cfg.candidate_sha256)) ||
            cfg.frozen_at_ms == 0 ||
            (cfg.source != "native_rendered_controlled_whitebox" &&
             cfg.source != "synthetic_fixture") ||
            cfg.intensity < 1 || cfg.intensity > 64 || cfg.count < 1 || cfg.count > 16 ||
            cfg.stride < 1 || cfg.stride > 16 || cfg.start < 0 ||
            int64_t(cfg.start) + int64_t(cfg.count - 1) * cfg.stride >= cfg.total_frames)
            return false;
        start_ms = clock_ns(CLOCK_REALTIME) / 1000000;
        if (start_ms <= cfg.frozen_at_ms)
            return false;
        dir = base + "/capture_" + cfg.run_id;
        if (mkdir(dir.c_str(), 0700) != 0)
            return false;
        return manifest(false);
    }
    bool selected(int frame) const {
        return frame >= cfg.start && (frame - cfg.start) % cfg.stride == 0 &&
               (frame - cfg.start) / cfg.stride < cfg.count;
    }
    bool frame(int frame_index, float frame_phase, uint64_t timestamp,
               const std::vector<Raw> &raw) {
        struct stat st{};
        if (lstat(dir.c_str(), &st) != 0 || !S_ISDIR(st.st_mode) || !failure_reason.empty() ||
            !selected(frame_index) || frame_index != cfg.start + int(rows.size()) * cfg.stride ||
            timestamp <= last_timestamp ||
            phase_bits(frame_phase) != phase_bits(phase(frame_index, cfg.total_frames)))
            return false;
        std::vector<std::string> wanted = {"native_lr", "native_hr"};
        if (!cfg.candidate_sha256.empty())
            wanted.push_back("postfx_candidate");
        if (raw.size() != wanted.size())
            return false;
        std::ostringstream row;
        row << "{\"frame_index\":" << frame_index
            << ",\"host_frame_id\":" << quote(cfg.run_id + ":" + std::to_string(frame_index))
            << ",\"timestamp_ns\":" << timestamp << ",\"phase\":" << std::setprecision(9)
            << frame_phase << ",\"phase_bits\":" << phase_bits(frame_phase)
            << ",\"iters\":" << cfg.intensity * 64
            << ",\"camera\":\"fullscreen_uv_identity\",\"exposure\":1,\"images\":[";
        for (size_t i = 0; i < raw.size(); i++) {
            const Raw &r = raw[i];
            if (r.role != wanted[i] || r.width == 0 || r.height == 0 || r.width > 8192 ||
                r.height > 8192 || uint64_t(r.width) * r.height > 32000000 || !r.bytes)
                return false;
            if (cfg.source != "synthetic_fixture" &&
                (r.width != (i == 0 ? 1280u : 1920u) || r.height != (i == 0 ? 720u : 1080u)))
                return false;
            size_t n = size_t(r.width) * r.height * 4;
            std::string name = std::to_string(frame_index) + "_" + r.role + ".rgba";
            int fd = open((dir + "/" + name).c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
            if (fd < 0)
                return sticky("raw_open_failed");
            bool ok = write_all(fd, r.bytes, n) && fsync(fd) == 0;
            int closed = close(fd);
            if (!ok || closed != 0)
                return sticky("raw_write_or_sync_failed");
            artifacts.push_back({name, digest(r.bytes, n), n});
            if (i)
                row << ',';
            row << "{\"role\":" << quote(r.role) << ",\"file\":" << quote(name)
                << ",\"width\":" << r.width << ",\"height\":" << r.height
                << ",\"row_bytes\":" << r.width * 4 << ",\"bytes\":" << n
                << ",\"sha256\":" << quote(digest(r.bytes, n))
                << ",\"vk_format\":\"R8G8B8A8_UNORM\",\"color_encoding\":\"linear_"
                   "shader_output\",\"source\":"
                << quote(cfg.source) << '}';
        }
        row << "]}";
        rows.push_back(row.str());
        last_timestamp = timestamp;
        return manifest(false);
    }
    bool manifest(bool clean) {
        end_ms = clock_ns(CLOCK_REALTIME) / 1000000;
        std::ostringstream j;
        j << "{\"schema\":\"refbench/native_paired_capture@1\",\"run_id\":" << quote(cfg.run_id)
          << ",\"scene\":\"sr_pipeline\",\"scene_content\":"
          << quote(cfg.source == "synthetic_fixture" ? "synthetic_fixture" : "procedural_alu")
          << ",\"source\":" << quote(cfg.source)
          << ",\"capture_mode\":\"quality_only\",\"performance_eligible\":false,"
             "\"real_game_source_attested\":false,\"framegen_intermediate_truth\":"
             "false,\"timestamp_scope\":\"CLOCK_MONOTONIC_before_command_recording_"
             "not_gpu_or_present\",\"capture_start_unix_ms\":"
          << start_ms << ",\"capture_end_unix_ms\":" << end_ms
          << ",\"rules_frozen_at_unix_ms\":" << cfg.frozen_at_ms
          << ",\"rules_sha256\":" << quote(cfg.rules_sha256)
          << ",\"scene_shader_sha256\":" << quote(cfg.scene_shader_sha256)
          << ",\"vertex_shader_sha256\":" << quote(cfg.vertex_shader_sha256)
          << ",\"candidate_sha256\":" << quote(cfg.candidate_sha256)
          << ",\"total_frames\":" << cfg.total_frames << ",\"intensity\":" << cfg.intensity
          << ",\"start\":" << cfg.start << ",\"count\":" << cfg.count
          << ",\"stride\":" << cfg.stride << ",\"failure_reason\":" << quote(failure_reason)
          << ",\"complete\":"
          << (clean && failure_reason.empty() && end_ms >= start_ms &&
                      rows.size() == size_t(cfg.count)
                  ? "true"
                  : "false")
          << ",\"frames\":[";
        for (size_t i = 0; i < rows.size(); i++) {
            if (i)
                j << ',';
            j << rows[i];
        }
        j << "]}\n";
        last_manifest_bytes = j.str();
        if (!atomic(dir + "/manifest.json", last_manifest_bytes))
            return sticky("manifest_write_or_sync_failed");
        return true;
    }
    bool finish(const std::string &run_output, int submitted, bool clean) {
        if (!clean || submitted != cfg.total_frames || rows.size() != size_t(cfg.count) ||
            !failure_reason.empty()) {
            sticky("run_aborted_or_incomplete"); manifest(false); return false;
        }
        for (const auto &a : artifacts)
            if (!file_matches(a.file, a.bytes, a.sha)) {
                sticky("raw_final_verification_failed"); manifest(false); return false;
            }
        if (!write_new("refbench_out.json", run_output)) {
            sticky("run_output_write_or_sync_failed"); manifest(false); return false;
        }
        if (!manifest(true)) return false;
        const std::string mh = digest(last_manifest_bytes.data(), last_manifest_bytes.size());
        if (last_manifest_bytes.size() > 1024 * 1024 || run_output.size() > 1024 * 1024 ||
            !file_matches("manifest.json", last_manifest_bytes.size(), mh) ||
            !file_matches("refbench_out.json", run_output.size(), digest(run_output.data(), run_output.size()))) {
            sticky("completion_file_verification_failed"); manifest(false); return false;
        }
        std::ostringstream receipt;
        receipt << "{\"schema\":\"refbench/native_capture_completion@1\",\"run_id\":" << quote(cfg.run_id)
                << ",\"manifest_sha256\":" << quote(mh)
                << ",\"run_output_sha256\":" << quote(digest(run_output.data(), run_output.size()))
                << ",\"frame_count\":" << rows.size() << ",\"frames_submitted\":" << submitted
                << ",\"total_frames\":" << cfg.total_frames
                << ",\"complete\":true,\"capture_mode\":\"quality_only\",\"performance_eligible\":false}\n";
        // Stage bytes privately, then link create-only: consumers never see a
        // partially written marker. The directory belongs to this run only.
        std::string tmp = "completion.json.tmp";
        if (!write_new(tmp, receipt.str()) ||
            link((dir + "/" + tmp).c_str(), (dir + "/completion.json").c_str()) != 0) {
            sticky("completion_publication_failed"); manifest(false); return false;
        }
        unlink((dir + "/" + tmp).c_str());
        int parent = open(dir.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
        bool synced = parent >= 0 && fsync(parent) == 0;
        if (parent >= 0 && close(parent) != 0) synced = false;
        if (!synced) {
            unlink((dir + "/completion.json").c_str());
            sticky("completion_directory_sync_failed"); manifest(false); return false;
        }
        return true;
    }
    bool failure_output(const std::string &run_output) {
        if (failure_reason.empty() || run_output.size() > 1024 * 1024) return false;
        return atomic(dir + "/refbench_out.json", run_output);
    }
    bool fail(const std::string &reason) {
        if (failure_reason.empty())
            failure_reason = reason;
        return manifest(false);
    }
    const std::string &directory() const { return dir; }
};
} // namespace paired_capture
