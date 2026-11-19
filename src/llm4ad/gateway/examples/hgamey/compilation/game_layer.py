"""Build an experimental present-stage layer with an exact adapted candidate."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def digest(path: Path) -> str:
    """Return the SHA-256 digest of an artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Compile and freeze a candidate without contacting a device."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--phonefarm", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--kind", choices=("seed", "diagnostic", "candidate"), required=True)
    parser.add_argument("--phonefarm-root", type=Path)
    parser.add_argument("--mode", choices=("native", "resize"), default="native")
    args = parser.parse_args()
    ndk = args.ndk.resolve(strict=True)
    phonefarm = args.phonefarm.resolve(strict=True)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = out / "candidate.comp"
    shutil.copyfile(args.source.resolve(strict=True), source)
    glslc = ndk / "shader-tools/linux-x86_64/glslc"
    cxx = ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang++"
    original = out / "original.spv"
    subprocess.run(
        [str(glslc), "--target-env=vulkan1.1", "-O", str(source), "-o", str(original)],
        check=True, timeout=60,
    )
    adapter = out / "adaptation"
    receipt = subprocess.check_output(
        [str(phonefarm), "gray-artifact", "adapt-storage", "--source", str(source),
         "--spirv", str(original), "--out", str(adapter), "--json"],
        timeout=60,
    )
    (out / "adaptation-receipt.json").write_bytes(receipt)
    adapter = Path(json.loads(receipt)["path"]).resolve(strict=True)
    if not adapter.is_relative_to(out / "adaptation"):
        raise ValueError("Adaptation escaped the owned output directory")
    subprocess.run(
        [str(phonefarm), "gray-artifact", "verify-adaptation", "--artifact", str(adapter)],
        check=True, timeout=60, stdout=subprocess.DEVNULL,
    )
    adapted = adapter / "adapted.spv"
    gray = out / "gray"
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(
        (args.phonefarm_root or repository / "native/vendor/hgamey/phonefarm") / "knobs/gray", gray,
        ignore=shutil.ignore_patterns("out", "__pycache__"),
    )
    env = dict(os.environ, REFBENCH_NDK=str(ndk))
    with (out / "native-build.log").open("w") as log:
        # Generate the shared modules using the repository's actual build recipe.
        subprocess.run(
            ["bash", str(gray / "build/build_layer.sh")], env=env,
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180,
        )
        generated = gray / "build/out"
        with (generated / "selfsr_spv.h").open("w") as header:
            for name, binary in (
                ("kSelfsrUpSpv", adapted),
                ("kSelfsrRcasSpv", generated / "selfsr_rcas.spv"),
            ):
                data = binary.read_bytes()
                header.write(f"alignas(4) static const unsigned char {name}[] = {{\n")
                for start in range(0, len(data), 16):
                    header.write(
                        "  " + ",".join(str(value) for value in data[start:start + 16]) + ",\n"
                    )
                header.write(f"}};\nstatic const unsigned int {name}Len = {len(data)};\n")
        metadata = {
            "kSelfsrCandidateSourceSha256": digest(source),
            "kSelfsrCandidateSpirvSha256": digest(adapted),
            "kSelfsrCandidateKind": args.kind,
        }
        (generated / "selfsr_candidate_metadata.h").write_text("".join(
            f"static const char {key}[] = {json.dumps(value)};\n"
            for key, value in metadata.items()
        ))
        config = json.loads((gray / "build/layer_build.json").read_text())
        layer = out / "libVkLayer_refknobs.so"
        subprocess.run(
            [str(cxx), *config["compile_flags"], "-I", str(generated), "-o", str(layer),
             *(str(gray / "layer" / name) for name in config["sources"]),
             *config["link_flags"]],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180,
        )
    frozen = [source, original, adapted, layer,
              generated / "selfsr_spv.h", generated / "selfsr_candidate_metadata.h"]
    frozen += sorted((gray / "layer").glob("*"))
    frozen += sorted(generated.glob("*"))
    frozen += [gray / "build/layer_build.json", gray / "build/build_layer.sh"]
    manifest = {
        "schema": "llm4ad/experimental-present-candidate@1",
        "kind": args.kind, "scope": ("game_present_upscaling" if args.mode == "resize"
                                      else "same_extent_present_stage_postprocessing"),
        "original_game_upscaler_replaced": False, "device_validation": "not_run",
        "source_sha256": digest(source), "adapted_spirv_sha256": digest(adapted),
        "layer_sha256": digest(layer),
        "files": {str(path.relative_to(out)): digest(path) for path in frozen if path.is_file()},
        "compiler_version": subprocess.check_output([str(cxx), "--version"], text=True),
        "ndk_properties": (ndk / "source.properties").read_text(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"artifact": str(out), "layer_sha256": manifest["layer_sha256"],
                      "device_validation": "not_run", "kind": args.kind}))


if __name__ == "__main__":
    main()
