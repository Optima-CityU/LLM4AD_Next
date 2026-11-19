import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path(os.environ.get("REFBENCH_EVIDENCE_DIR", str(ROOT / "tasks/native_capture_20261011")))
SPEC = importlib.util.spec_from_file_location("native_pairs", ROOT / "tools/native_pairs.py")
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)


def rules():
    return {"schema": "phonefarm/sequence_quality_rules@1", "protocol_id": "host-synthetic-not-game-v1", "frozen_at_unix_ms": time.time_ns()//1000000 - 5000, "thresholds": {"min_frames": 4, "max_pair_timestamp_delta_ns": 0, "max_frame_gap_ns": 1000000000, "max_alignment_error_px": 0.0, "min_psnr_db": 30.0, "max_channel_bias": 2.0, "max_temporal_residual_mae": 2.0, "min_reference_motion_mae": 0.01, "max_reference_motion_mae": 64.0}, "exclude_rects": []}


class NativePairs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="refbench-native-pairs-test-")
        cls.base = Path(cls.temp.name)
        cls.binary = cls.base / "capture_test"
        subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti", "-I"+str(ROOT / "app/src"), str(ROOT / "tests/paired_capture_test.cpp"), "-o", str(cls.binary)], check=True)
        cls.rule_path = cls.base / "rules.json"
        cls.rule_path.write_bytes(N.json_bytes(rules()))
        cls.candidate = cls.base / "synthetic_candidate.spv"
        cls.candidate.write_bytes(b"explicit fake non-GPU executable fixture")
        subprocess.run([str(cls.binary), str(cls.base), str(cls.rule_path), str(cls.candidate)], check=True, capture_output=True)
        cls.original = cls.base / "capture_host_fixture"
        cls.manifest = json.loads((cls.original / "manifest.json").read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.case = self.base / self._testMethodName
        shutil.copytree(self.original, self.case)
        self.path = self.case / "manifest.json"

    def change(self, fn):
        m = copy.deepcopy(self.manifest)
        fn(m)
        self.path.write_bytes(N.json_bytes(m))

    def load(self):
        return N.load_capture(self.path, self.rule_path, self.candidate)

    def rebind_completion(self, output_change=None):
        output_path = self.case / "refbench_out.json"
        output = json.loads(output_path.read_bytes())
        if output_change:
            output_change(output)
        output_path.write_bytes(N.json_bytes(output))
        receipt_path = self.case / "completion.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["manifest_sha256"] = N.sha(self.path.read_bytes())
        receipt["run_output_sha256"] = N.sha(output_path.read_bytes())
        receipt_path.write_bytes(N.json_bytes(receipt))

    def test_completion_marker_required_and_bound(self):
        marker = self.case / "completion.json"
        marker.unlink()
        with self.assertRaisesRegex(ValueError, "completion.json"):
            self.load()
        marker.write_bytes((self.original / "completion.json").read_bytes())
        receipt = json.loads(marker.read_bytes())
        receipt["run_id"] = "old_run"
        marker.write_bytes(N.json_bytes(receipt))
        with self.assertRaisesRegex(ValueError, "run/schema"):
            self.load()

    def test_output_semantics_rejected_even_with_rehashed_marker(self):
        for change, error in [
            (lambda o: o.update(run_id="stale"), "identity"),
            (lambda o: o.update(clean_exit=False), "incomplete"),
            (lambda o: o.update(frames_submitted=59), "incomplete"),
            (lambda o: o.update(frames_submitted=True), "incomplete"),
            (lambda o: o.update(params={"frames":60,"intensity":64}), "configuration"),
            (lambda o: o.update(postfx={"active":False}), "candidate"),
        ]:
            (self.case / "refbench_out.json").write_bytes((self.original / "refbench_out.json").read_bytes())
            self.rebind_completion(change)
            with self.assertRaisesRegex(ValueError, error):
                self.load()

    def test_disk_failures_remain_unpublished(self):
        for name in ("out_collision", "manifest_collision", "raw_tamper", "disk_full", "symlink_guard"):
            capture = self.base / ("capture_" + name)
            self.assertFalse((capture / "completion.json").exists(), name)
        for name in ("out_collision", "raw_tamper"):
            output = json.loads((self.base / ("capture_" + name) / "refbench_out.json").read_bytes())
            self.assertFalse(output["clean_exit"])
        manifest = json.loads((self.base / "capture_out_collision/manifest.json").read_bytes())
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["failure_reason"], "run_output_write_or_sync_failed")
        self.assertEqual((self.base / "capture_marker_collision/completion.json").read_text(), "stale")
        with self.assertRaises(ValueError):
            N.load_capture(self.base / "capture_marker_collision/manifest.json", self.rule_path, self.candidate)

    def test_completion_marker_symlink_and_fifo_rejected_without_block(self):
        marker = self.case / "completion.json"
        marker.unlink()
        marker.symlink_to(self.original / "completion.json")
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            self.load()
        marker.unlink()
        os.mkfifo(marker)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            self.load()

    def test_actual_cpp_sink_and_png_bridge(self):
        m, *_ = self.load()
        self.assertTrue(m["complete"])
        self.assertGreater(m["capture_start_unix_ms"], m["rules_frozen_at_unix_ms"])
        out = self.case / "bridge"
        result = N.import_capture(self.path, self.rule_path, self.candidate, out)
        self.assertEqual(result["source"], "synthetic_fixture")
        ref = json.loads((out / "reference.json").read_bytes())
        cand = json.loads((out / "candidate.json").read_bytes())
        prov = json.loads((out / "import_provenance.json").read_bytes())
        self.assertEqual(ref["captured_at_unix_ms"], m["capture_start_unix_ms"])
        self.assertEqual(ref["frames"][0]["timestamp_ns"], m["frames"][0]["timestamp_ns"])
        self.assertEqual(ref["pairing_evidence"], cand["pairing_evidence"])
        self.assertEqual(prov["capture_manifest_sha256"], N.sha(self.path.read_bytes()))
        self.assertEqual(prov["frames"][0]["images"][0]["role"], "native_lr")
        self.assertFalse(prov["real_game_qualification"])
        self.assertFalse(prov["performance_eligible"])
        self.assertEqual((out / ref["frames"][0]["file"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(FileExistsError):
            N.import_capture(self.path, self.rule_path, self.candidate, out)

    def test_incomplete_and_dropped_frame_rejected(self):
        incomplete = self.base / "capture_incomplete/manifest.json"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            N.load_capture(incomplete, self.rule_path, self.candidate)
        failed = self.base / "capture_failed/manifest.json"
        negative = json.loads(failed.read_bytes())
        self.assertFalse(negative["complete"])
        self.assertEqual(negative["failure_reason"], "injected_submission_fence_timeout")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            N.load_capture(failed, self.rule_path, self.candidate)
        self.change(lambda m: m["frames"].pop())
        with self.assertRaisesRegex(ValueError, "count"):
            self.load()

    def test_phase_and_conditions_rejected(self):
        self.change(lambda m: m["frames"][1].update(phase=1.0))
        with self.assertRaisesRegex(ValueError, "phase"):
            self.load()
        self.change(lambda m: m["frames"][1].update(camera="moved"))
        with self.assertRaisesRegex(ValueError, "conditions"):
            self.load()

    def test_clock_and_rules_binding_rejected(self):
        self.change(lambda m: m.update(capture_start_unix_ms=m["rules_frozen_at_unix_ms"]))
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.load()
        self.change(lambda m: m.update(rules_sha256="a"*64))
        with self.assertRaisesRegex(ValueError, "binding"):
            self.load()
        self.change(lambda m: m["frames"][1].update(timestamp_ns=m["frames"][0]["timestamp_ns"]))
        with self.assertRaisesRegex(ValueError, "timestamp"):
            self.load()

    def test_identity_candidate_and_raw_tampering_rejected(self):
        self.change(lambda m: m.update(candidate_sha256="a"*64))
        with self.assertRaisesRegex(ValueError, "candidate artifact"):
            self.load()
        self.change(lambda m: None)
        raw = self.case / self.manifest["frames"][0]["images"][0]["file"]
        raw.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "bytes/hash"):
            self.load()

    def test_source_claims_native_dimensions_and_roles_rejected(self):
        self.change(lambda m: m.update(real_game_source_attested=True))
        with self.assertRaisesRegex(ValueError, "claim scope"):
            self.load()
        self.change(lambda m: m.update(source=N.SOURCE_NATIVE, scene_content="procedural_alu"))
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            self.load()
        def fake_native(m):
            m["source"] = N.SOURCE_NATIVE
            m["scene_content"] = "procedural_alu"
            for frame in m["frames"]:
                for image in frame["images"]:
                    image["source"] = N.SOURCE_NATIVE
        self.change(fake_native)
        with self.assertRaisesRegex(ValueError, "native dimensions"):
            self.load()
        self.change(lambda m: m["frames"][0]["images"][1].update(role="postfx_candidate"))
        with self.assertRaisesRegex(ValueError, "role"):
            self.load()

    def test_traversal_symlink_duplicate_fields_rejected(self):
        self.change(lambda m: m["frames"][0]["images"][0].update(file="../escape.rgba"))
        with self.assertRaisesRegex(ValueError, "file name"):
            self.load()
        self.change(lambda m: None)
        raw = self.case / self.manifest["frames"][0]["images"][0]["file"]
        raw.unlink()
        raw.symlink_to(self.original / raw.name)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            self.load()
        self.path.write_text('{"schema":"a","schema":"b"}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load()

    def test_color_transfer_and_alpha_contract(self):
        self.assertEqual(N.SRGB_LUT[0], 0)
        self.assertEqual(N.SRGB_LUT[255], 255)
        self.assertEqual(N.SRGB_LUT[128], 188)
        with self.assertRaisesRegex(ValueError, "alpha"):
            N.png(b"\x01\x02\x03\x00", 1, 1)
        for w, h in ((2, 4), (4, 2)):
            data = N.png(bytes([128, 0, 255, 255])*w*h, w, h)
            self.assertEqual(struct.unpack(">II", data[16:24]), (w, h))

    def test_native_pairs_without_candidate_are_assets_only(self):
        def off(m):
            m["candidate_sha256"] = ""
            for frame in m["frames"]:
                frame["images"].pop()
        self.change(off)
        self.rebind_completion(lambda o: o.update(effective_knobs={"postfx":"off"}, postfx={"active":False}))
        N.load_capture(self.path, self.rule_path)
        with self.assertRaisesRegex(ValueError, "requires an executed"):
            N.import_capture(self.path, self.rule_path, None, self.case / "out")

    def test_cli_strict_failure_and_success(self):
        cmd = ["python3", str(ROOT / "tools/native_pairs.py"), "validate", "--manifest", str(self.path), "--rules", str(self.rule_path), "--candidate-shader", str(self.candidate)]
        ok = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertFalse(json.loads(ok.stdout)["real_game_qualification"])
        import_cmd = cmd.copy()
        import_cmd[2] = "import"
        import_cmd.extend(["--out", str(self.case / "cli_import")])
        imported = subprocess.run(import_cmd, capture_output=True, text=True)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["source"], "synthetic_fixture")
        self.assertFalse(json.loads(imported.stdout)["real_game_qualification"])
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "import-cli-stdout.json").write_text(imported.stdout)
        self.change(lambda m: m.update(complete=False))
        bad = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)

    @unittest.skipUnless(os.environ.get("PHONEFARM_SEQUENCE_BINARY"), "set existing consumer binary for offline integration")
    def test_existing_phonefarm_consumer_metrics_not_game_proof(self):
        out = self.case / "consumer"
        N.import_capture(self.path, self.rule_path, self.candidate, out)
        command = [os.environ["PHONEFARM_SEQUENCE_BINARY"], "sequence-quality", "--reference", str(out / "reference.json"), "--candidate", str(out / "candidate.json"), "--rules", str(out / "frozen_rules.json"), "--out", str(out / "report.json")]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["metrics_verdict"], "PASS")
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["source_validity"], "NON_REAL")
        self.assertEqual(report["pairing_verification"], "ledger_consistent")
        self.assertTrue(report["artifact_bound"])
        self.assertFalse(report["real_game_qualification"])
        self.assertFalse(report["real_game_source_pair"])
        self.assertFalse(report["no_ghosting_proven"])
        self.assertEqual(len(report["frames"]), 4)
        # Preserve actual executable input/output as reviewable local evidence.
        evidence = EVIDENCE / "consumer_fixture"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        if evidence.exists(): shutil.rmtree(evidence)  # only this generated test fixture
        shutil.copytree(out, evidence)
        (evidence / "stdout.json").write_text(result.stdout)
        (evidence / "stderr.txt").write_text(result.stderr)
        (evidence / "exit.json").write_text(json.dumps({"exit_code": result.returncode, "executable": command[0], "fixture": "CPU-synthetic, not native GPU or game capture"}))
        # Conservative unknown-source boundary, using the same explicitly CPU
        # fixture. This is not relabeled as an actual native capture.
        unknown = self.case / "consumer_unknown"
        shutil.copytree(out, unknown)
        for name in ("reference.json", "candidate.json"):
            obj = json.loads((unknown / name).read_bytes())
            obj["source"]["kind"] = "unknown"
            (unknown / name).write_bytes(N.json_bytes(obj))
        unknown_result = subprocess.run([command[0], "sequence-quality", "--reference", str(unknown / "reference.json"), "--candidate", str(unknown / "candidate.json"), "--rules", str(unknown / "frozen_rules.json"), "--out", str(unknown / "unknown_report.json")], capture_output=True, text=True)
        self.assertEqual(unknown_result.returncode, 3, unknown_result.stderr)
        unknown_report = json.loads(unknown_result.stdout)
        self.assertEqual(unknown_report["metrics_verdict"], "PASS")
        self.assertEqual(unknown_report["verdict"], "UNKNOWN")
        self.assertFalse(unknown_report["real_game_qualification"])
        self.assertFalse(unknown_report["no_ghosting_proven"])
        unknown_evidence = evidence.parent / "consumer_unknown"
        if unknown_evidence.exists(): shutil.rmtree(unknown_evidence)
        shutil.copytree(unknown, unknown_evidence)
        (unknown_evidence / "stdout.json").write_text(unknown_result.stdout)
        (unknown_evidence / "stderr.txt").write_text(unknown_result.stderr)
        (unknown_evidence / "exit.json").write_text(json.dumps({"exit_code": unknown_result.returncode, "executable": command[0], "fixture": "CPU synthetic with conservative unknown source; not GPU capture"}))



if __name__ == "__main__":
    unittest.main()
