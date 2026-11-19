"""Production JSON writer known answers; host facts only, no GPU/device proof."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OutputContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="refbench-output-contract-")
        cls.addClassCleanup(cls.temp.cleanup)
        binary = Path(cls.temp.name) / "output_contract_test"
        compile_result = subprocess.run(
            ["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
             "-I" + str(ROOT / "app/src"), str(ROOT / "tests/output_contract_test.cpp"), "-o", str(binary)],
            capture_output=True, text=True)
        if compile_result.returncode:
            raise RuntimeError("host fixture compile failed:\n" + compile_result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if os.environ.get("REFBENCH_EVIDENCE_DIR"):
            evidence = Path(os.environ["REFBENCH_EVIDENCE_DIR"])
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "output-contract-stdout.json").write_text(result.stdout)
            (evidence / "output-contract-stderr.txt").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError("host fixture failed:\n" + result.stderr)
        cls.outputs = {row["case"]: row["output"] for row in json.loads(result.stdout)}
        cls.schema = json.loads((ROOT / "contract/output.schema.json").read_text())

    def test_four_scene_known_answers(self):
        for case, scene, bottleneck, knobs, size in (
                ("bw_off", "bw_pingpong", "bandwidth", {"loadop": "off"}, (2048, 2048)),
                ("bw_on", "bw_pingpong", "bandwidth", {"loadop": "on"}, (2048, 2048)),
                ("fragment", "frag_alu", "fragment", {}, (1024, 1024)),
                ("idle", "idle_cap", "none", {}, (0, 0)),
                ("sr_off", "sr_pipeline", "fragment+compute", {"postfx": "off"}, (1280, 720))):
            with self.subTest(case=case):
                out = self.outputs[case]
                self.assertEqual(out["scene"], scene)
                self.assertEqual(out["declared_bottleneck"], bottleneck)
                self.assertEqual(out["effective_knobs"], knobs)
                self.assertEqual(out["params"], {"frames": 60, "intensity": 8,
                                                 "rt_width": size[0], "rt_height": size[1]})
                self.assertEqual(out["frames_submitted"], 60)
                self.assertIs(out["clean_exit"], True)
                self.assertNotIn("capture_mode", out)
                self.assertNotIn("performance_eligible", out)
                if scene != "sr_pipeline":
                    self.assertNotIn("postfx", out)
        self.assertEqual(self.outputs["bw_off"]["passes"][0]["load_op"], "LOAD")
        self.assertEqual(self.outputs["bw_on"]["passes"][0]["load_op"], "DONT_CARE")

    def test_sr_candidate_and_capture_scope(self):
        self.assertIs(self.outputs["sr_off"]["postfx"]["active"], False)
        on = self.outputs["sr_on"]
        self.assertEqual(on["effective_knobs"], {"postfx": "on"})
        self.assertEqual(on["postfx"], {"active": True, "shader_path": "/data/local/tmp/fixture.spv",
                                        "shader_bytes": 1024, "lo": [1280, 720],
                                        "hi": [1920, 1080], "workgroup": [16, 16, 1]})
        self.assertEqual(on["passes"][1]["load_op"], "NONE")
        capture = self.outputs["sr_capture"]
        self.assertEqual(capture["capture_mode"], "quality_only")
        self.assertIs(capture["performance_eligible"], False)
        self.assertEqual(capture["passes"][-1]["target"], "nativeHR")
        failed = self.outputs["sr_failed"]
        self.assertEqual(failed["effective_knobs"], {"postfx": "on"})
        self.assertIs(failed["postfx"]["active"], False)
        self.assertIs(failed["clean_exit"], False)
        self.assertEqual(failed["frames_submitted"], 0)

    def test_long_strings_round_trip_without_truncation(self):
        out = self.outputs["long_strings"]
        self.assertEqual(out["postfx"]["shader_path"], ("/" + "p" * 180) * 5 + '/shader"\\\n\t.spv')
        self.assertEqual(out["run_id"], 'run\0id"\\\n')
        self.assertEqual(out["gpu_name"], 'host "GPU" \\ \t 渲染')
        self.assertEqual(out["passes"][1]["name"], "n" * 600 + '"\\\n')
        self.assertIs(out["clean_exit"], True)  # trailer survives long preceding fields

    def test_schema_required_fields_and_declared_enums(self):
        # Check the contract surfaces exercised here, not a home-grown full
        # JSON Schema implementation. In particular, real compute metadata
        # must not be rejected by the public load_op vocabulary.
        schema = self.schema
        for name, out in self.outputs.items():
            with self.subTest(case=name):
                self.assertTrue(set(schema["required"]).issubset(out))
                for field in ("contract_version", "render_thread_name", "driver_submit_thread_name"):
                    self.assertEqual(out[field], schema["properties"][field]["const"])
                for field in ("scene", "declared_bottleneck"):
                    self.assertIn(out[field], schema["properties"][field]["enum"])
                for field in ("params", "swapchain"):
                    self.assertTrue(set(schema["properties"][field]["required"]).issubset(out[field]))
                for field, value in out["effective_knobs"].items():
                    self.assertIn(value, schema["properties"]["effective_knobs"]["properties"][field]["enum"])
                pass_schema = schema["properties"]["passes"]["items"]
                for item in out["passes"]:
                    self.assertTrue(set(pass_schema["required"]).issubset(item))
                    for field in ("load_op", "store_op"):
                        self.assertIn(item[field], pass_schema["properties"][field]["enum"])
                if "capture_mode" in out:
                    self.assertEqual(out["capture_mode"], schema["properties"]["capture_mode"]["const"])
                    self.assertIs(out["performance_eligible"], schema["properties"]["performance_eligible"]["const"])


if __name__ == "__main__":
    unittest.main()
