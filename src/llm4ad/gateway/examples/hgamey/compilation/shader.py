"""Compile GLSL and validate the fixed runner SPIR-V interface."""

import struct
import subprocess
from contextlib import suppress
from pathlib import Path


def compile_shader(source: Path, output: Path, *, compiler: str, validator: str, timeout: float) -> None:
    """Compile real GLSL and validate the emitted SPIR-V before dispatch."""
    commands = [
        [compiler, "-V", "--target-env", "vulkan1.0", "-S", "comp", "-o", str(output), str(source)],
        [validator, "--target-env", "vulkan1.0", str(output)],
    ]
    for index, command in enumerate(commands):
        log = output.parent / f"{output.stem}.tool-{index}.log"
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            with suppress(OSError):
                log.write_bytes(
                    (stdout.encode() if isinstance(stdout, str) else stdout)
                    + (stderr.encode() if isinstance(stderr, str) else stderr)
                )
            raise
        if result.returncode:
            with suppress(OSError):
                log.write_text(result.stdout + result.stderr, encoding="utf-8")
            raise ValueError((result.stdout + result.stderr).strip())
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if output.read_bytes()[:4] != b"\x03\x02\x23\x07":
        raise ValueError("Compiler did not produce a SPIR-V module")
    validate_interface(output.read_bytes())


def validate_interface(binary: bytes) -> None:
    """Check the compiled module against the trusted Vulkan runner slots.

    Args:
        binary: A module already accepted by spirv-val.
    """
    words = struct.unpack(f"<{len(binary) // 4}I", binary)
    decorations, pointers, images, variables = {}, {}, {}, []
    index = 5
    while index < len(words):
        length, opcode = words[index] >> 16, words[index] & 0xFFFF
        if not length or index + length > len(words):
            raise ValueError("Malformed SPIR-V instruction")
        args = words[index + 1 : index + length]
        if opcode == 71:
            decorations.setdefault(args[0], {})[args[1]] = args[2:]
        elif opcode == 32:
            pointers[args[0]] = (args[1], args[2])
        elif opcode == 25:
            images[args[0]] = args[1:]
        elif opcode == 59:
            variables.append((args[0], args[1], args[2]))
        elif opcode == 15 and args[0] != 5:
            raise ValueError("Only a compute entry point is supported by the Shader runner")
        index += length
    slots = set()
    for result_type, variable, storage in variables:
        if storage in {2, 9, 10, 12}:
            raise ValueError("Uniform buffers, push constants and storage buffers are not runner resources")
        if storage != 0:
            continue
        attributes = decorations.get(variable, {})
        binding, descriptor_set = attributes.get(33), attributes.get(34)
        if descriptor_set != (0,) or binding not in {(0,), (1,)} or binding in slots:
            raise ValueError("Runner resources must be exactly descriptor set 0 bindings 0 and 1")
        image = images.get(pointers.get(result_type, (None, None))[1])
        if not image or image[1:7] != (1, 0, 0, 0, 2, 4):
            raise ValueError("Runner descriptors must be non-arrayed rgba8 storage image2D values")
        access = 24 if binding == (0,) else 25
        if access not in attributes:
            raise ValueError("Runner input is readonly and output is writeonly")
        slots.add(binding)
    if slots != {(0,), (1,)}:
        raise ValueError("Both runner image bindings are required")
