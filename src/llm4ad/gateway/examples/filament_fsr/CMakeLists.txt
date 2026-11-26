cmake_minimum_required(VERSION 3.18)
project(filament_fsr LANGUAGES CXX ASM)

set(FILAMENT_SDK "" CACHE PATH "Extracted Filament Android native release")
set(FILAMENT_SOURCE "" CACHE PATH "Matching Filament source checkout")
set(FILAMENT_HOST_TOOLS "" CACHE PATH "Matching Filament host bin directory")
set(FILAMENT_FLIP_INCLUDE "" CACHE PATH "Directory containing NVIDIA FLIP 1.7 FLIP.h")
set(filament_libs "${FILAMENT_SDK}/lib/${ANDROID_ABI}")
if(NOT ANDROID OR NOT EXISTS "${filament_libs}/libfilament.a")
    message(FATAL_ERROR "Use the Android NDK and set FILAMENT_SDK to the official Android native release")
endif()

set(fsr_source "${FILAMENT_SOURCE}/filament/src/materials/fsr")
set(STOCK_MATERIAL "${CMAKE_CURRENT_BINARY_DIR}/stock-easu.filamat")
set(material_assembly "${CMAKE_CURRENT_BINARY_DIR}/stock-easu.S")
configure_file(material.S.in "${material_assembly}" @ONLY)
add_custom_command(OUTPUT "${STOCK_MATERIAL}"
    COMMAND "${FILAMENT_HOST_TOOLS}/matc" -a opengl -a vulkan -p mobile
        -o "${STOCK_MATERIAL}" "${fsr_source}/fsr_easu_mobile.mat"
    DEPENDS "${fsr_source}/fsr_easu_mobile.mat" "${fsr_source}/ffx_fsr1_mobile.fs"
        "${fsr_source}/ffx_a.h" "${fsr_source}/ffx_fsr1.h"
    VERBATIM)
set_source_files_properties("${material_assembly}" PROPERTIES OBJECT_DEPENDS "${STOCK_MATERIAL}")

add_executable(filament_fsr_baseline benchmark.cpp compare_images.cpp select_material.cpp "${material_assembly}")
target_compile_features(filament_fsr_baseline PRIVATE cxx_std_20)
target_include_directories(filament_fsr_baseline SYSTEM PRIVATE
    "${FILAMENT_SDK}/include" "${FILAMENT_SOURCE}/third_party/stb" "${FILAMENT_SOURCE}/libs/imagediff/include")
target_link_directories(filament_fsr_baseline PRIVATE "${filament_libs}")
target_link_libraries(filament_fsr_baseline PRIVATE
    -Wl,--start-group
    filament backend filabridge filaflat shaders utils gltfio_core ktxreader image basis_transcoder
    geometry mikktspace meshoptimizer uberarchive uberzlib stb dracodec bluevk smol-v zstd
    perfetto abseil ibl-lite filameshio imagediff
    -Wl,--end-group
    android log EGL GLESv3 dl)
target_link_options(filament_fsr_baseline PRIVATE
    "-Wl,--strip-debug" "-Wl,--wrap=_ZN8filament8Material7Builder7packageEPKvm")

if(FILAMENT_FLIP_INCLUDE)
    file(SHA256 "${FILAMENT_FLIP_INCLUDE}/FLIP.h" flip_sha256)
    if(NOT flip_sha256 STREQUAL "412b118db343a3a0d030104f17f46c1e3edc7455d2b83a7398a42e25befed104")
        message(FATAL_ERROR "Use FLIP.h from the flip-evaluator 1.7 source release")
    endif()
    target_sources(filament_fsr_baseline PRIVATE flip_images.cpp)
    target_include_directories(filament_fsr_baseline SYSTEM PRIVATE "${FILAMENT_FLIP_INCLUDE}")
    target_compile_definitions(filament_fsr_baseline PRIVATE FILAMENT_EVALUATE_FLIP)
    set_source_files_properties(flip_images.cpp PROPERTIES COMPILE_OPTIONS "-fopenmp")
    target_link_options(filament_fsr_baseline PRIVATE "-fopenmp" "-static-openmp")
endif()
