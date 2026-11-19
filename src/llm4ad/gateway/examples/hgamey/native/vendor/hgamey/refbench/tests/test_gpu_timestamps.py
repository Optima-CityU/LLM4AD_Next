"""Host-only raw timestamp contract; does not run Vulkan or qualify a phone."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GpuTimestamps(unittest.TestCase):
    def test_flight_slots_do_not_reorder_or_reuse_frame_evidence(self):
        source = r'''
#include "gpu_timestamps.h"
#include <cassert>
#include <iostream>
int main() {
    refbench_timestamps::Capture c;
    assert(!c.configure(2, 0, 1));
    assert(!c.configure(2, 65, 1));
    assert(!c.configure(2, 64, 0));
    assert(c.configure(2, 36, 2.5));
    assert(c.record(1, 14, 21));
    assert(!c.complete());
    assert(!c.record(1, 100, 200));
    assert(!c.record(2, 100, 200));
    std::cout << c.json("partial") << '\n';
    assert(c.record(0, (1ull << 36) - 4, 3));
    assert(c.complete());
    std::cout << c.json("run\"id") << '\n';
}
'''
        with tempfile.TemporaryDirectory() as folder:
            src, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            src.write_text(source)
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(ROOT / "app/src"), str(src), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
        partial, complete = map(json.loads, result.stdout.splitlines())
        self.assertFalse(partial["complete"])
        self.assertEqual(partial["samples"], [[1, 14, 21]])
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["samples"], [[0, (1 << 36) - 4, 3], [1, 14, 21]])
        self.assertEqual(complete["run_id"], 'run"id')
        self.assertEqual(complete["timestamp_period_ns"], 2.5)
