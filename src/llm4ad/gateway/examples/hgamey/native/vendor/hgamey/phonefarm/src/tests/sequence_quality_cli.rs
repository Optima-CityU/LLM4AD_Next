//! Program-generated fixtures exercise the actual module and actual phonefarm CLI.
//! No fixture is real-game acceptance evidence.
#[path = "../sequence_quality.rs"]
#[allow(dead_code)]
mod sequence_quality;

use image::{Rgb, RgbImage};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);

fn sha(path: &Path) -> String {
    format!("{:x}", Sha256::digest(std::fs::read(path).unwrap()))
}

fn write_json(path: &Path, v: &Value) {
    std::fs::write(path, serde_json::to_vec_pretty(v).unwrap()).unwrap();
}

struct Fixture {
    root: PathBuf,
    reference: Value,
    candidate: Value,
    rules: Value,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

impl Fixture {
    fn new(w: u32, h: u32, mode: &str) -> Self {
        let root = std::env::temp_dir().join("tasks").join(format!(
            "pf-quality-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&root).unwrap();
        let rules = json!({
            "schema":"phonefarm/sequence_quality_rules@1", "protocol_id":"synthetic-test-v1", "frozen_at_unix_ms":100,
            "thresholds":{
                "min_frames":4, "max_pair_timestamp_delta_ns":1000000, "max_frame_gap_ns":50000000,
                "max_alignment_error_px":0.25, "min_psnr_db":30.0, "max_channel_bias":3.0,
                "max_temporal_residual_mae":0.25, "min_reference_motion_mae":0.25, "max_reference_motion_mae":32.0
            }, "exclude_rects":[]
        });
        write_json(&root.join("rules.json"), &rules);
        let rules_hash = sha(&root.join("rules.json"));
        let mut rframes = Vec::new();
        let mut cframes = Vec::new();
        let mut previous: Option<RgbImage> = None;
        for t in 0..4u64 {
            let r = RgbImage::from_fn(w, h, |x, _| {
                let phase = if mode == "static" { 0 } else { t as u32 };
                let v = if (x + phase) % w < w / 2 { 100 } else { 80 };
                Rgb([v, v, v])
            });
            let c = RgbImage::from_fn(w, h, |x, y| {
                let mut p = r.get_pixel(x, y).0;
                match mode {
                    "bias" => p[0] += 12,
                    "ghost" => {
                        if let Some(prev) = &previous {
                            for k in 0..3 {
                                p[k] = ((p[k] as u16 + prev.get_pixel(x, y)[k] as u16) / 2) as u8;
                            }
                        }
                    }
                    "flicker" => {
                        if t % 2 == 1 {
                            for x in &mut p {
                                *x += 2;
                            }
                        }
                    }
                    _ => (),
                }
                Rgb(p)
            });
            let frame = |role: &str, im: &RgbImage| {
                let file = format!("{role}_{t}.png");
                im.save(root.join(&file)).unwrap();
                json!({"frame_id":t,"timestamp_ns":t*16666667,"file":file,"sha256":sha(&root.join(&file)),"width":w,"height":h,
                    "alignment":{"method":"exact_pair","reference_frame_id":t,"error_px":0.0,"evidence":"synthetic fixture generator; not real scene attestation"}})
            };
            rframes.push(frame("r", &r));
            cframes.push(frame("c", &c));
            previous = Some(r);
        }
        let manifest = |role: &str, frames: Vec<Value>| {
            json!({
                "schema":"phonefarm/paired_frame_sequence@1", "protocol_id":"synthetic-test-v1", "rules_sha256":rules_hash,
                "sequence_id":"synthetic-sequence", "timeline_id":"synthetic-clock", "role":role,"captured_at_unix_ms":200,
                "color_space":"sRGB8", "content_scope":"scene_only",
                "source":{"kind":"synthetic_fixture","provenance":"program-generated integration fixture",
                    "capture_method":"fixture generator","device":null,"package":null,"game_version":null,"derivation":null},
                "frames":frames
            })
        };
        let mut f = Self {
            root,
            reference: manifest("reference", rframes),
            candidate: manifest("candidate", cframes),
            rules,
        };
        std::fs::write(
            f.root.join("candidate.fixture"),
            "synthetic generator candidate; not a deployable game shader",
        )
        .unwrap();
        f.candidate["candidate_artifact"] =
            json!({"file":"candidate.fixture","sha256":sha(&f.root.join("candidate.fixture"))});
        f.bind_ledger();
        f.persist();
        f
    }

    fn bind_ledger(&mut self) {
        let pairs: Vec<Value> = self.reference["frames"].as_array().unwrap().iter()
            .zip(self.candidate["frames"].as_array().unwrap()).map(|(r,c)| json!({
                "frame_id":r["frame_id"],"timestamp_ns":r["timestamp_ns"],"candidate_timestamp_ns":c["timestamp_ns"],"reference_sha256":r["sha256"],"candidate_sha256":c["sha256"]
            })).collect();
        let ledger = json!({"schema":"phonefarm/frame_pair_ledger@1","sequence_id":"synthetic-sequence","timeline_id":"synthetic-clock",
            "capture_method":"same_frame_dual_output","provenance":"program-generated fixture ledger; not real capture", "candidate_artifact_sha256":self.candidate["candidate_artifact"]["sha256"], "pairs":pairs});
        write_json(&self.root.join("ledger.json"), &ledger);
        let binding = json!({"file":"ledger.json", "sha256":sha(&self.root.join("ledger.json"))});
        self.reference["pairing_evidence"] = binding.clone();
        self.candidate["pairing_evidence"] = binding;
    }

    fn persist(&mut self) {
        write_json(&self.root.join("rules.json"), &self.rules);
        let rules_hash = sha(&self.root.join("rules.json"));
        self.reference["rules_sha256"] = json!(rules_hash);
        self.candidate["rules_sha256"] = self.reference["rules_sha256"].clone();
        write_json(&self.root.join("reference.json"), &self.reference);
        write_json(&self.root.join("candidate.json"), &self.candidate);
    }

    fn evaluate(&self) -> sequence_quality::Report {
        sequence_quality::evaluate(
            &self.root.join("reference.json"),
            &self.root.join("candidate.json"),
            &self.root.join("rules.json"),
        )
        .unwrap()
    }

    fn cli(&self) -> std::process::Output {
        std::process::Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .current_dir(&self.root)
            .args([
                "sequence-quality",
                "--reference",
                "reference.json",
                "--candidate",
                "candidate.json",
                "--rules",
                "rules.json",
                "--out",
                "report.json",
                "--json",
            ])
            .output()
            .unwrap()
    }
}

#[test]
fn actual_cli_landscape_portrait_and_replay() {
    for (w, h) in [(16, 9), (9, 16)] {
        let f = Fixture::new(w, h, "identical");
        let first = f.cli();
        assert_eq!(
            first.status.code(),
            Some(0),
            "{}",
            String::from_utf8_lossy(&first.stderr)
        );
        let report: Value = serde_json::from_slice(&first.stdout).unwrap();
        assert_eq!(report["metrics_verdict"], "PASS");
        assert_eq!(report["source_validity"], "NON_REAL");
        assert_eq!(report["pairing_verification"], "ledger_consistent");
        assert_eq!(report["real_game_qualification"], false);
        assert_eq!(report["artifact_bound"], true);
        assert_eq!(report["no_ghosting_proven"], false);
        assert_eq!(report["frames"][0]["metrics"]["pixels"], w * h);
        assert_eq!(report["frames"][0]["metrics"]["exact_match"], true);
        assert!(report["frames"][0]["metrics"]["psnr_db"].is_null());
        let second = f.cli();
        assert_eq!(second.status.code(), Some(0));
        assert_eq!(first.stdout, second.stdout);
        assert_eq!(
            first.stdout,
            std::fs::read(f.root.join("report.json")).unwrap()
        );
    }
}

#[test]
fn color_bias_and_temporal_ghost_have_explainable_frames() {
    let f = Fixture::new(16, 9, "bias");
    let r = f.evaluate();
    assert_eq!(r.verdict, "FAIL");
    assert_eq!(
        r.frames[0].metrics.as_ref().unwrap().channel_bias,
        [12.0, 0.0, 0.0]
    );
    assert!(r.frames[0]
        .violations
        .contains(&"CHANNEL_BIAS_ABOVE_LIMIT".to_string()));
    assert_eq!(f.cli().status.code(), Some(1));
    let mut f = Fixture::new(16, 9, "ghost");
    f.rules["thresholds"]["min_psnr_db"] = json!(0.0);
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.verdict, "FAIL");
    assert!(r.transitions.iter().any(|x| x
        .violations
        .contains(&"TEMPORAL_RESIDUAL_ABOVE_LIMIT".to_string())));
    assert!(r.transitions[0].temporal_residual_mae > 0.25);
    assert!(r.frames[1]
        .candidate
        .as_ref()
        .unwrap()
        .file
        .ends_with("1.png"));
}

#[test]
fn flicker_is_detected_when_each_spatial_frame_passes() {
    let f = Fixture::new(16, 9, "flicker");
    let r = f.evaluate();
    assert!(r.frames.iter().all(|x| x.violations.is_empty()));
    assert_eq!(r.metrics_verdict, "FAIL");
    assert!(r.transitions.iter().all(|x| x.temporal_residual_mae > 1.0));
}

#[test]
fn missing_frame_timestamp_mismatch_and_dimensions_never_pass() {
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate["frames"].as_array_mut().unwrap().remove(1);
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.verdict, "UNKNOWN");
    assert!(r
        .issues
        .iter()
        .any(|x| x.code == "FRAME_PAIR_INVALID" && x.frame_id == Some(1)));
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate["frames"][1]["timestamp_ns"] = json!(999999999u64);
    f.persist();
    assert_eq!(f.evaluate().metrics_verdict, "UNKNOWN");
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate["frames"][1]["width"] = json!(9);
    f.persist();
    assert_eq!(f.evaluate().verdict, "UNKNOWN");
    assert_eq!(f.cli().status.code(), Some(3));
}

#[test]
fn provenance_and_ledger_are_separate_from_metrics() {
    let mut f = Fixture::new(16, 9, "identical");
    f.reference.as_object_mut().unwrap().remove("source");
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.metrics_verdict, "PASS");
    assert_eq!(r.verdict, "UNKNOWN");
    assert_eq!(r.source_validity, "UNKNOWN");
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate
        .as_object_mut()
        .unwrap()
        .remove("pairing_evidence");
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.metrics_verdict, "PASS");
    assert_eq!(r.verdict, "UNKNOWN");
    assert_eq!(r.pairing_verification, "unknown");
    let mut f = Fixture::new(16, 9, "identical");
    for manifest in [&mut f.reference, &mut f.candidate] {
        manifest["source"] = json!({"kind":"real_game_capture","provenance":"declared fake source for trust-boundary test", "capture_method":"claimed RT dump","device":"synthetic-device", "package":"fake.game", "game_version":"test","derivation":null});
    }
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.source_validity, "DECLARED_REAL");
    assert_eq!(r.acquisition_attestation, "not_checked");
    assert!(!r.real_game_qualification);
    assert!(r.declared_real_game_source_pair);
    assert!(!r.real_game_source_pair);
}

#[test]
fn candidate_artifact_binding_is_required_and_crosschecked() {
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate
        .as_object_mut()
        .unwrap()
        .remove("candidate_artifact");
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.metrics_verdict, "PASS");
    assert_eq!(r.verdict, "UNKNOWN");
    assert!(!r.artifact_bound);
    let f = Fixture::new(16, 9, "identical");
    std::fs::write(f.root.join("candidate.fixture"), "different candidate").unwrap();
    let r = f.evaluate();
    assert!(!r.artifact_bound);
    assert_eq!(r.verdict, "UNKNOWN");
    assert!(r
        .issues
        .iter()
        .any(|x| x.code == "CANDIDATE_ARTIFACT_INVALID"));
}

#[test]
fn hash_corruption_alignment_and_high_motion_are_unknown() {
    let f = Fixture::new(16, 9, "identical");
    std::fs::write(f.root.join("c_1.png"), b"bad png").unwrap();
    assert_eq!(f.evaluate().metrics_verdict, "UNKNOWN");
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate["frames"][1]["alignment"]["method"] = json!("unverified");
    f.persist();
    assert_eq!(f.evaluate().verdict, "UNKNOWN");
    let mut f = Fixture::new(16, 9, "identical");
    f.rules["thresholds"]["max_reference_motion_mae"] = json!(1.0);
    f.persist();
    let r = f.evaluate();
    assert_eq!(r.metrics_verdict, "UNKNOWN");
    assert!(r
        .issues
        .iter()
        .any(|x| x.code == "MOTION_OR_PAIRING_OUT_OF_RANGE"));
    let f = Fixture::new(16, 9, "static");
    assert!(f
        .evaluate()
        .issues
        .iter()
        .any(|x| x.code == "INSUFFICIENT_MOTION_COVERAGE"));
}

#[test]
fn derived_and_hud_sources_cannot_be_real_game_qualification() {
    for kind in ["hr_downsampled", "synthetic_hud_composite"] {
        let mut f = Fixture::new(16, 9, "identical");
        for m in [&mut f.reference, &mut f.candidate] {
            m["source"]["kind"] = json!(kind);
            m["source"]["derivation"] = json!("synthetic pipeline derivation");
        }
        f.persist();
        let r = f.evaluate();
        assert_eq!(r.metrics_verdict, "PASS");
        assert_eq!(r.source_validity, "NON_REAL");
        assert!(!r.real_game_source_pair);
        assert!(!r.real_game_qualification);
    }
}

#[test]
fn frozen_rules_and_path_traversal_do_not_silently_pass() {
    let mut f = Fixture::new(16, 9, "identical");
    f.reference["rules_sha256"] = json!("0".repeat(64));
    write_json(&f.root.join("reference.json"), &f.reference);
    assert!(f
        .evaluate()
        .issues
        .iter()
        .any(|x| x.code == "RULES_BINDING_MISMATCH"));
    let mut f = Fixture::new(16, 9, "identical");
    f.candidate["frames"][1]["file"] = json!("../c_1.png");
    f.persist();
    assert_eq!(f.evaluate().verdict, "UNKNOWN");
    let mut f = Fixture::new(16, 9, "identical");
    f.rules["frozen_at_unix_ms"] = json!(200);
    f.persist();
    assert_eq!(f.evaluate().verdict, "UNKNOWN");
    f.rules["thresholds"]["min_frames"] = json!(1);
    f.persist();
    assert_eq!(f.cli().status.code(), Some(2));
}

#[test]
fn empty_masks_and_report_overwrite_are_rejected() {
    let mut f = Fixture::new(16, 9, "identical");
    f.rules["exclude_rects"] = json!([[0.0, 0.0, 1.0, 1.0]]);
    f.persist();
    assert_eq!(f.evaluate().verdict, "UNKNOWN");
    let f = Fixture::new(16, 9, "identical");
    std::fs::write(f.root.join("report.json"), "older evidence").unwrap();
    assert_eq!(f.cli().status.code(), Some(2));
    assert_eq!(
        std::fs::read_to_string(f.root.join("report.json")).unwrap(),
        "older evidence"
    );
}

#[test]
fn hash_consistent_malformed_and_16bit_images_are_unknown() {
    for mode in ["malformed", "16bit"] {
        let mut f = Fixture::new(16, 9, "identical");
        let path = f.root.join("c_1.png");
        if mode == "16bit" {
            image::ImageBuffer::<image::Rgb<u16>, Vec<u16>>::from_pixel(
                16,
                9,
                image::Rgb([100, 100, 100]),
            )
            .save(&path)
            .unwrap();
        } else {
            std::fs::write(&path, b"\x89PNG\r\n\x1a\nmalformed").unwrap();
        }
        f.candidate["frames"][1]["sha256"] = json!(sha(&path));
        f.bind_ledger();
        f.persist();
        let r = f.evaluate();
        assert_eq!(r.pairing_verification, "ledger_consistent");
        assert_eq!(r.metrics_verdict, "UNKNOWN");
        let issue = r
            .issues
            .iter()
            .find(|i| i.code == "FRAME_PAIR_INVALID" && i.frame_id == Some(1))
            .unwrap();
        assert!(
            issue.detail.contains(if mode == "16bit" {
                "8-bit"
            } else {
                "decode failed"
            }),
            "{}",
            issue.detail
        );
    }
}
