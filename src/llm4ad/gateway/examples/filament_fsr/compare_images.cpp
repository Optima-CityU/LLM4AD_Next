#include <imagediff/ImageDiff.h>
#include <stb_image.h>

#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>

int compare_images(const char* reference, const char* candidate) {
    int width0, height0, width1, height1, channels;
    std::unique_ptr<stbi_uc, decltype(&stbi_image_free)> a(stbi_load(reference, &width0, &height0, &channels, 4), stbi_image_free);
    std::unique_ptr<stbi_uc, decltype(&stbi_image_free)> b(stbi_load(candidate, &width1, &height1, &channels, 4), stbi_image_free);
    if (!a || !b) {
        throw std::runtime_error("Could not decode an EASU comparison frame");
    }
    imagediff::Bitmap origin{uint32_t(width0), uint32_t(height0), size_t(width0) * 4, a.get()};
    imagediff::Bitmap changed{uint32_t(width1), uint32_t(height1), size_t(width1) * 4, b.get()};
    auto result = imagediff::compare(origin, changed, {});
    std::cout << imagediff::serializeResult(result).c_str() << '\n';
    return 0;
}
