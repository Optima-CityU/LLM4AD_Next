#include <backend/PixelBufferDescriptor.h>
#include <filament/Camera.h>
#include <filament/Engine.h>
#include <filament/IndirectLight.h>
#include <filament/Renderer.h>
#include <filament/Scene.h>
#include <filament/SwapChain.h>
#include <filament/View.h>
#include <filament/Viewport.h>
#include <gltfio/Animator.h>
#include <gltfio/AssetLoader.h>
#include <gltfio/FilamentAsset.h>
#include <gltfio/FilamentInstance.h>
#include <gltfio/MaterialProvider.h>
#include <gltfio/ResourceLoader.h>
#include <gltfio/TextureProvider.h>
#include <gltfio/materials/uberarchive.h>
#include <image/Ktx1Bundle.h>
#include <ktxreader/Ktx1Reader.h>
#include <utils/EntityManager.h>

#include "baseline/benchmark_settings.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <numbers>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include <stb_image_write.h>

using namespace filament;
using namespace filament::gltfio;

namespace {

std::vector<uint8_t> readFile(const char* path) {
    std::ifstream input(path, std::ios::binary);
    std::vector<uint8_t> bytes(std::istreambuf_iterator<char>{input}, {});
    if (bytes.empty()) {
        throw std::runtime_error(std::string("Could not read ") + path);
    }
    return bytes;
}

}  // namespace

int compare_images(const char* reference, const char* candidate);
#ifdef FILAMENT_EVALUATE_FLIP
int compare_flip_images(const char* reference, const char* stock, const char* candidate, const char* preview_prefix);
#endif
void load_candidate_material(const char* path);
void verify_easu_material(bool upscaling);

int main(int argc, char** argv) {
    try {
        if (argc == 4 && std::string_view(argv[1]) == "--diff") {
            return compare_images(argv[2], argv[3]);
        }
        if ((argc == 5 || argc == 6) && std::string_view(argv[1]) == "--flip") {
#ifdef FILAMENT_EVALUATE_FLIP
            return compare_flip_images(argv[2], argv[3], argv[4], argc == 6 ? argv[5] : nullptr);
#else
            throw std::runtime_error("Rebuild with FILAMENT_FLIP_INCLUDE for LDR-FLIP");
#endif
        }
        if ((argc != 5 && argc != 6)
                || (std::string_view(argv[4]) != "DamagedHelmet" && std::string_view(argv[4]) != "Fox")) {
            throw std::invalid_argument(
                    "Usage: filament_fsr_baseline SCENE.glb LIGHT.ktx OUTPUT_DIR DamagedHelmet|Fox "
                    "[CANDIDATE|--native-reference]");
        }
        const bool native = argc == 6 && std::string_view(argv[5]) == "--native-reference";
        if (argc == 6 && !native) {
            load_candidate_material(argv[5]);
        }
        const bool fox = std::string_view(argv[4]) == "Fox";
        const auto glb = readFile(argv[1]);
        const auto ibl = readFile(argv[2]);

        Engine* engine = Engine::create(Engine::Backend::VULKAN);
        if (!engine || engine->getBackend() != Engine::Backend::VULKAN) {
            throw std::runtime_error("Could not start the Filament Vulkan backend");
        }
        auto* renderer = engine->createRenderer();
        auto* swapChain = engine->createSwapChain(WIDTH, HEIGHT, SwapChain::CONFIG_READABLE);
        auto* scene = engine->createScene();
        auto* view = engine->createView();
        auto& entities = utils::EntityManager::get();
        auto cameraEntity = entities.create();
        auto* camera = engine->createCamera(cameraEntity);

        auto materials = std::unique_ptr<MaterialProvider>(
                createUbershaderProvider(engine, UBERARCHIVE_DEFAULT_DATA, UBERARCHIVE_DEFAULT_SIZE));
        auto textureProvider = std::unique_ptr<TextureProvider>(createStbProvider(engine));
        auto* loader = AssetLoader::create({.engine = engine, .materials = materials.get()});
        auto* asset = loader->createAsset(glb.data(), glb.size());
        if (!asset) {
            throw std::runtime_error("Could not load the official glTF sample");
        }
        {
            ResourceLoader resources({.engine = engine});
            resources.addTextureProvider("image/png", textureProvider.get());
            resources.addTextureProvider("image/jpeg", textureProvider.get());
            if (!resources.loadResources(asset)) {
                throw std::runtime_error("Could not load the glTF resources");
            }
        }
        scene->addEntities(asset->getEntities(), asset->getEntityCount());
        auto* animator = fox ? asset->getInstance()->getAnimator() : nullptr;
        size_t walk = 0;
        if (animator) {
            while (walk < animator->getAnimationCount() && std::string_view(animator->getAnimationName(walk)) != "Walk") {
                ++walk;
            }
            if (walk == animator->getAnimationCount()) {
                throw std::runtime_error("The Fox asset has no Walk animation");
            }
        }

        auto* bundle = new image::Ktx1Bundle(ibl.data(), ibl.size());
        math::float3 sh[9]{};
        if (!bundle->getSphericalHarmonics(sh)) {
            delete bundle;
            throw std::runtime_error("The Filament IBL asset has no spherical harmonics");
        }
        auto* reflection = ktxreader::Ktx1Reader::createTexture(engine, bundle, false);
        auto* light = IndirectLight::Builder().reflections(reflection).irradiance(3, sh)
                .intensity(30000.0f).build(*engine);
        scene->setIndirectLight(light);

        auto bounds = asset->getBoundingBox();
        auto extent = bounds.max - bounds.min;
        float radius = 0.5f * std::max({extent.x, extent.y, extent.z});
        auto target = math::double3(bounds.center());
        const auto setFrame = [&](int frame) {
            const double fraction = double(frame) / TIMED_FRAMES;
            if (animator) {
                animator->applyAnimation(walk, static_cast<float>(fraction) * animator->getAnimationDuration(walk));
                animator->updateBoneMatrices();
            }
            const double angle = fox ? 0.8 : 0.4 + 2.0 * std::numbers::pi * fraction;
            auto offset = math::double3(3.9 * std::sin(angle), 0.6, 3.9 * std::cos(angle));
            camera->lookAt(target + offset * double(radius), target);
        };
        camera->setProjection(42.0, double(WIDTH) / HEIGHT, 0.05 * radius, 30.0 * radius);
        view->setCamera(camera);
        view->setScene(scene);
        view->setViewport({0, 0, WIDTH, HEIGHT});
        view->setMultiSampleAntiAliasingOptions({.enabled = true, .sampleCount = 4});
        view->setDithering(Dithering::NONE);
        view->setDynamicResolutionOptions({
            .minScale = {RENDER_SCALE, RENDER_SCALE},
            .maxScale = {RENDER_SCALE, RENDER_SCALE},
            .enabled = !native,
            .homogeneousScaling = true,
            .quality = QualityLevel::HIGH,
        });
        if (view->getDynamicResolutionOptions().enabled == native) {
            throw std::runtime_error("Filament did not apply the requested render scale");
        }
        renderer->setClearOptions({.clearColor = {0.04f, 0.06f, 0.08f, 1.0f}, .clear = true});

        std::map<uint32_t, int64_t> gpuSamples;
        const auto collect = [&] {
            for (const auto& frame : renderer->getFrameInfoHistory(renderer->getMaxFrameHistorySize())) {
                if (frame.gpuFrameDuration > 0) {
                    gpuSamples[frame.frameId] = frame.gpuFrameDuration;
                }
            }
        };
        const int renderFrames = native ? WARMUP_FRAMES : WARMUP_FRAMES + TIMED_FRAMES + 4;
        for (int i = 0; i < renderFrames; ++i) {
            setFrame(std::max(0, i - WARMUP_FRAMES) % TIMED_FRAMES);
            (void)renderer->beginFrame(swapChain);
            renderer->render(view);
            renderer->endFrame();
            // Retire the frame before reusing Vulkan timer queries in the next iteration.
            engine->flushAndWait();
            collect();
        }
        // Renderer reserves IDs 0 and 1 before the first submitted frame.
        gpuSamples.erase(gpuSamples.begin(), gpuSamples.lower_bound(WARMUP_FRAMES + 2));
        gpuSamples.erase(gpuSamples.upper_bound(WARMUP_FRAMES + TIMED_FRAMES + 1), gpuSamples.end());
        verify_easu_material(!native);

        // Revisit the fixed timeline after timing, so readbacks and PNG encoding stay outside measurements.
        std::vector<uint8_t> pixels(WIDTH * HEIGHT * 4);
        for (int frame = 0; !std::getenv("LLM4AD_FSR_TIMINGS_ONLY") && frame < TIMED_FRAMES; frame += CAPTURE_STEP) {
            setFrame(frame);
            (void)renderer->beginFrame(swapChain);
            renderer->render(view);
            renderer->readPixels(0, 0, WIDTH, HEIGHT, backend::PixelBufferDescriptor(
                    pixels.data(), pixels.size(), backend::PixelDataFormat::RGBA, backend::PixelDataType::UBYTE));
            renderer->endFrame();
            engine->flushAndWait();
            char filename[32];
            std::snprintf(filename, sizeof(filename), "/frame-%03d.png", frame);
            const auto path = std::string(argv[3]) + filename;
            if (!stbi_write_png(path.c_str(), WIDTH, HEIGHT, 4, pixels.data(), WIDTH * 4)) {
                throw std::runtime_error("Could not write the rendered frame " + path);
            }
        }

        engine->destroy(view);
        engine->destroy(renderer);
        engine->destroy(swapChain);
        engine->destroy(scene);
        engine->destroyCameraComponent(cameraEntity);
        entities.destroy(cameraEntity);
        loader->destroyAsset(asset);
        AssetLoader::destroy(&loader);
        materials->destroyMaterials();
        materials.reset();
        textureProvider.reset();
        engine->destroy(light);
        engine->destroy(reflection);
        Engine::destroy(&engine);

        std::cout << "BACKEND Vulkan\n";
        std::cout << (native ? "MODE native\n" : "MODE fsr\n");
        std::cout << "SCENE " << argv[4] << '\n';
        for (const auto& [frame, ns] : gpuSamples) {
            std::cout << "GPU_FRAME_NS " << frame << ' ' << ns << '\n';
        }
        if (!native && gpuSamples.size() != TIMED_FRAMES) {
            throw std::runtime_error("Filament did not report every requested GPU frame duration: " +
                    std::to_string(gpuSamples.size()));
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}
