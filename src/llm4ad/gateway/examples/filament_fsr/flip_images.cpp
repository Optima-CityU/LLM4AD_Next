#include <FLIP.h>
#include <stb_image.h>
#include <stb_image_write.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr float pixelsPerDegree = 67.0f;

struct Image {
    int width = 0;
    int height = 0;
    std::unique_ptr<stbi_uc, decltype(&stbi_image_free)> rgb{nullptr, stbi_image_free};

    explicit Image(const char* path) {
        rgb.reset(stbi_load(path, &width, &height, nullptr, 3));
        if (!rgb || width <= 0 || height <= 0) {
            throw std::runtime_error(std::string("Could not decode an LDR-FLIP frame: ") + path);
        }
    }

    std::size_t size() const {
        return std::size_t(width) * height * 3;
    }

    bool isFlatColor() const {
        constexpr int maxFlatChannelSpan = 1;
        std::array<stbi_uc, 3> minimum{rgb.get()[0], rgb.get()[1], rgb.get()[2]};
        auto maximum = minimum;
        const auto* end = rgb.get() + size();
        for (const auto* pixel = rgb.get() + 3; pixel < end; pixel += 3) {
            for (std::size_t channel = 0; channel < minimum.size(); ++channel) {
                minimum[channel] = std::min(minimum[channel], pixel[channel]);
                maximum[channel] = std::max(maximum[channel], pixel[channel]);
                if (maximum[channel] - minimum[channel] > maxFlatChannelSpan) {
                    return false;
                }
            }
        }
        return true;
    }

    std::vector<float> linearRgb() const {
        static const auto srgbToLinear = [] {
            std::array<float, 256> table{};
            for (std::size_t i = 0; i < table.size(); ++i) {
                table[i] = FLIP::color3::sRGBToLinearRGB(float(i) / 255.0f);
            }
            return table;
        }();
        std::vector<float> linear(size());
        std::transform(rgb.get(), rgb.get() + size(), linear.begin(),
                [](stbi_uc encoded) { return srgbToLinear[encoded]; });
        return linear;
    }
};

float compute_error(const Image& reference, const std::vector<float>& referenceLinear,
        const Image& variant, const std::string& preview) {
    FLIP::Parameters params;
    params.PPD = pixelsPerDegree;
    float mean = 0.0f;
    float* result = nullptr;
    const auto variantLinear = variant.linearRgb();
    FLIP::evaluate(referenceLinear.data(), variantLinear.data(), reference.width, reference.height,
            false, params, !preview.empty(), true, mean, &result);
    const std::unique_ptr<float[]> errorMap(result);
    if (!std::isfinite(mean) || mean < 0.0f || mean > 1.0f) {
        throw std::runtime_error("LDR-FLIP reported an invalid mean error");
    }
    if (!preview.empty()) {
        std::vector<uint8_t> encoded(reference.size());
        std::transform(errorMap.get(), errorMap.get() + encoded.size(), encoded.begin(),
                [](float value) { return uint8_t(std::lround(std::clamp(value, 0.0f, 1.0f) * 255.0f)); });
        if (!stbi_write_png(preview.c_str(), reference.width, reference.height, 3, encoded.data(), reference.width * 3)) {
            throw std::runtime_error("Could not write the LDR-FLIP error map");
        }
    }
    return mean;
}

}  // namespace

int compare_flip_images(const char* referencePath, const char* stockPath, const char* candidatePath,
        const char* previewPrefix) {
    const Image reference(referencePath);
    const Image stock(stockPath);
    const Image candidate(candidatePath);
    for (const auto* image : {&stock, &candidate}) {
        if (image->width != reference.width || image->height != reference.height) {
            throw std::runtime_error("LDR-FLIP requires three equal-size frames");
        }
    }

    const auto referenceLinear = reference.linearRgb();
    const std::string stockMap = previewPrefix ? std::string(previewPrefix) + "-stock-flip.png" : "";
    const std::string candidateMap = previewPrefix ? std::string(previewPrefix) + "-candidate-flip.png" : "";
    const float stockError = compute_error(reference, referenceLinear, stock, stockMap);
    const bool same = std::memcmp(stock.rgb.get(), candidate.rgb.get(), stock.size()) == 0;
    const float candidateError = same && !previewPrefix
            ? stockError : compute_error(reference, referenceLinear, candidate, candidateMap);
    std::cout << std::setprecision(std::numeric_limits<float>::max_digits10)
              << "{\"stock_mean_error\":" << stockError << ",\"candidate_mean_error\":" << candidateError
              << ",\"candidate_flat_color\":" << (candidate.isFlatColor() ? "true" : "false") << "}\n";
    return 0;
}
