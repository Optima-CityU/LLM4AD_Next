//! Offline, paired-sequence quality evidence. See https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_SEQUENCE_QUALITY.md
//! RGB metrics and residual-change checks are bounded diagnostics, not a proof of no ghosting.

use image::{ImageReader, RgbImage};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Cursor;
use std::path::{Component, Path};

const MAX_FILE: u64 = 64 * 1024 * 1024;
const MAX_PIXELS: u64 = 32 * 1024 * 1024;
const MAX_FRAMES: usize = 10000;
const MANIFEST_SCHEMA: &str = "phonefarm/paired_frame_sequence@1";
const RULES_SCHEMA: &str = "phonefarm/sequence_quality_rules@1";
const LEDGER_SCHEMA: &str = "phonefarm/frame_pair_ledger@1";
const USAGE: &str = "phonefarm sequence-quality --reference reference.json --candidate candidate.json --rules frozen_rules.json [--out tasks/quality/report.json] [--json]";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Thresholds {
    pub min_frames: usize,
    pub max_pair_timestamp_delta_ns: u64,
    pub max_frame_gap_ns: u64,
    pub max_alignment_error_px: f64,
    pub min_psnr_db: f64,
    pub max_channel_bias: f64,
    pub max_temporal_residual_mae: f64,
    pub min_reference_motion_mae: f64,
    pub max_reference_motion_mae: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Rules {
    pub schema: String,
    pub protocol_id: String,
    pub frozen_at_unix_ms: u64,
    pub thresholds: Thresholds,
    pub exclude_rects: Vec<[f64; 4]>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum SourceKind {
    #[default]
    Unknown,
    RealGameCapture,
    SyntheticFixture,
    HrDownsampled,
    SyntheticHudComposite,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    #[serde(default)]
    pub kind: SourceKind,
    #[serde(default)]
    pub provenance: String,
    #[serde(default)]
    pub capture_method: String,
    #[serde(default)]
    pub device: Option<String>,
    #[serde(default)]
    pub package: Option<String>,
    #[serde(default)]
    pub game_version: Option<String>,
    #[serde(default)]
    pub derivation: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Alignment {
    pub method: String,
    pub reference_frame_id: u64,
    pub error_px: f64,
    pub evidence: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Frame {
    pub frame_id: u64,
    pub timestamp_ns: u64,
    pub file: String,
    pub sha256: String,
    pub width: u32,
    pub height: u32,
    pub alignment: Alignment,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactBinding {
    pub file: String,
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema: String,
    pub protocol_id: String,
    pub rules_sha256: String,
    pub sequence_id: String,
    pub timeline_id: String,
    pub role: String,
    pub captured_at_unix_ms: u64,
    pub color_space: String,
    pub content_scope: String,
    #[serde(default)]
    pub source: Source,
    #[serde(default)]
    pub pairing_evidence: Option<ArtifactBinding>,
    #[serde(default)]
    pub candidate_artifact: Option<ArtifactBinding>,
    pub frames: Vec<Frame>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PairLedger {
    schema: String,
    sequence_id: String,
    timeline_id: String,
    capture_method: String,
    provenance: String,
    candidate_artifact_sha256: String,
    pairs: Vec<PairRow>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PairRow {
    frame_id: u64,
    timestamp_ns: u64,
    candidate_timestamp_ns: u64,
    reference_sha256: String,
    candidate_sha256: String,
}

#[derive(Debug, Serialize)]
pub struct Issue {
    pub code: String,
    pub frame_id: Option<u64>,
    pub detail: String,
    pub invalidates_measurement: bool,
}

#[derive(Debug, Serialize)]
pub struct SpatialMetrics {
    pub psnr_db: Option<f64>,
    pub exact_match: bool,
    pub mse: f64,
    pub channel_bias: [f64; 3],
    pub pixels: u64,
}

#[derive(Debug, Serialize)]
pub struct FrameEvidence {
    pub frame_id: u64,
    pub reference: Frame,
    pub candidate: Option<Frame>,
    pub pair_timestamp_delta_ns: Option<u64>,
    pub metrics: Option<SpatialMetrics>,
    pub violations: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct TransitionEvidence {
    pub previous_frame_id: u64,
    pub frame_id: u64,
    pub reference_motion_mae: f64,
    pub temporal_residual_mae: f64,
    pub violations: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct Report {
    pub schema: String,
    pub verdict: String,
    pub metrics_verdict: String,
    pub source_validity: String,
    pub pairing_verification: String,
    pub pairing_capture_method: Option<String>,
    pub acquisition_attestation: String,
    pub real_game_qualification: bool,
    pub declared_real_game_source_pair: bool,
    pub artifact_bound: bool,
    pub candidate_artifact: Option<ArtifactBinding>,
    pub real_game_source_pair: bool,
    pub no_ghosting_proven: bool,
    pub scope: String,
    pub limitations: Vec<String>,
    pub protocol_id: String,
    pub sequence_id: String,
    pub content_scope: String,
    pub reference_source: Source,
    pub candidate_source: Source,
    pub rules_sha256: String,
    pub reference_manifest_sha256: String,
    pub candidate_manifest_sha256: String,
    pub pairing_evidence_sha256: Option<String>,
    pub rules: Rules,
    pub issues: Vec<Issue>,
    pub frames: Vec<FrameEvidence>,
    pub transitions: Vec<TransitionEvidence>,
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn read_bounded(path: &Path) -> Result<Vec<u8>, String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("{}: {e}", path.display()))?;
    if !meta.is_file() || meta.len() > MAX_FILE {
        return Err("artifact is not a regular file or exceeds 64 MiB".into());
    }
    std::fs::read(path).map_err(|e| e.to_string())
}

fn read_json<T: for<'de> Deserialize<'de>>(p: &Path) -> Result<(T, String), String> {
    let b = read_bounded(p)?;
    let v = serde_json::from_slice(&b).map_err(|e| format!("{}: {e}", p.display()))?;
    Ok((v, hash(&b)))
}

fn valid_hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn artifact_bytes(base: &Path, binding: &ArtifactBinding) -> Result<Vec<u8>, String> {
    if !valid_hash(&binding.sha256) {
        return Err("invalid sha256 (expected lowercase 64 hex characters)".into());
    }
    let p = Path::new(&binding.file);
    if p.as_os_str().is_empty() || p.components().any(|c| !matches!(c, Component::Normal(_))) {
        return Err("artifact path must be relative without traversal".into());
    }
    let root = base.canonicalize().map_err(|e| e.to_string())?;
    let path = base.join(p).canonicalize().map_err(|e| e.to_string())?;
    if !path.starts_with(&root) {
        return Err("artifact symlink escapes manifest directory".into());
    }
    let bytes = read_bounded(&path)?;
    if hash(&bytes) != binding.sha256 {
        return Err("artifact sha256 mismatch".into());
    }
    Ok(bytes)
}

fn load_image(base: &Path, f: &Frame) -> Result<RgbImage, String> {
    if f.width == 0
        || f.height == 0
        || f.width > 8192
        || f.height > 8192
        || u64::from(f.width) * u64::from(f.height) > MAX_PIXELS
    {
        return Err("invalid or oversized declared image dimensions".into());
    }
    let bytes = artifact_bytes(
        base,
        &ArtifactBinding {
            file: f.file.clone(),
            sha256: f.sha256.clone(),
        },
    )?;
    let mut reader = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()
        .map_err(|e| e.to_string())?;
    if !matches!(
        reader.format(),
        Some(image::ImageFormat::Png | image::ImageFormat::Jpeg)
    ) {
        return Err("input must be PNG or JPEG under the frozen sRGB8 contract".into());
    }
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(8192);
    limits.max_image_height = Some(8192);
    limits.max_alloc = Some(256 * 1024 * 1024);
    reader.limits(limits);
    let im = reader
        .decode()
        .map_err(|e| format!("image decode failed: {e}"))?;
    if im.width() != f.width || im.height() != f.height {
        return Err("decoded dimensions differ from manifest".into());
    }
    if u64::from(im.width()) * u64::from(im.height()) > MAX_PIXELS {
        return Err("decoded image exceeds pixel limit".into());
    }
    if !matches!(
        im.color(),
        image::ColorType::Rgb8
            | image::ColorType::Rgba8
            | image::ColorType::L8
            | image::ColorType::La8
    ) {
        return Err("input must have 8-bit channels; no implicit HDR/16-bit conversion".into());
    }
    if im.color().has_alpha() && im.to_rgba8().pixels().any(|p| p[3] != 255) {
        return Err("nonopaque image needs an explicit, preregistered compositing contract".into());
    }
    Ok(im.to_rgb8())
}

fn issue(
    out: &mut Vec<Issue>,
    code: &str,
    frame: Option<u64>,
    detail: impl Into<String>,
    measurement: bool,
) {
    out.push(Issue {
        code: code.into(),
        frame_id: frame,
        detail: detail.into(),
        invalidates_measurement: measurement,
    });
}

fn validate_rules(r: &Rules) -> Result<(), String> {
    let t = &r.thresholds;
    if r.schema != RULES_SCHEMA || r.protocol_id.trim().is_empty() || r.frozen_at_unix_ms == 0 {
        return Err("invalid rules schema, protocol_id or freeze timestamp".into());
    }
    if t.min_frames < 3 || t.min_frames > MAX_FRAMES || t.max_frame_gap_ns == 0 {
        return Err("min_frames must be 3..10000 and max_frame_gap_ns must be positive".into());
    }
    let numeric = [
        t.max_alignment_error_px,
        t.min_psnr_db,
        t.max_channel_bias,
        t.max_temporal_residual_mae,
        t.min_reference_motion_mae,
        t.max_reference_motion_mae,
    ];
    if numeric.iter().any(|x| !x.is_finite() || *x < 0.0)
        || t.min_psnr_db > 100.0
        || t.max_channel_bias > 255.0
        || t.max_temporal_residual_mae > 510.0
        || t.min_reference_motion_mae <= 0.0
        || t.max_reference_motion_mae > 255.0
        || t.min_reference_motion_mae >= t.max_reference_motion_mae
    {
        return Err(
            "thresholds must be finite, physically bounded and have a nonempty motion interval"
                .into(),
        );
    }
    for rect in &r.exclude_rects {
        if rect
            .iter()
            .any(|x| !x.is_finite() || !(0.0..=1.0).contains(x))
            || rect[0] >= rect[2]
            || rect[1] >= rect[3]
        {
            return Err("exclude_rects must be normalized, nonempty rectangles".into());
        }
    }
    Ok(())
}

fn validate_manifest(
    m: &Manifest,
    role: &str,
    rules: &Rules,
    rules_hash: &str,
    issues: &mut Vec<Issue>,
) -> Result<(), String> {
    if m.schema != MANIFEST_SCHEMA || m.role != role || m.frames.len() > MAX_FRAMES {
        return Err(format!("invalid {role} schema/role/frame count"));
    }
    if m.sequence_id.trim().is_empty() || m.timeline_id.trim().is_empty() {
        return Err(format!(
            "{role} sequence_id and timeline_id must be nonempty"
        ));
    }
    if m.protocol_id != rules.protocol_id || m.rules_sha256 != rules_hash {
        issue(issues, "RULES_BINDING_MISMATCH", None, role, true);
    }
    if m.captured_at_unix_ms <= rules.frozen_at_unix_ms {
        issue(issues, "RULES_NOT_PREREGISTERED", None, role, true);
    }
    if m.color_space != "sRGB8"
        || !matches!(
            m.content_scope.as_str(),
            "scene_only" | "full_frame_with_hud" | "masked_scene"
        )
    {
        issue(issues, "UNSUPPORTED_PIXEL_SCOPE", None, role, true);
    }
    if m.content_scope == "masked_scene" && rules.exclude_rects.is_empty() {
        issue(issues, "MASK_MISSING", None, role, true);
    }
    for (i, f) in m.frames.iter().enumerate() {
        if i > 0 {
            let prev = &m.frames[i - 1];
            if prev.frame_id.checked_add(1) != Some(f.frame_id) {
                issue(
                    issues,
                    "FRAME_ID_GAP_OR_ORDER",
                    Some(f.frame_id),
                    role,
                    true,
                );
            }
            match f.timestamp_ns.checked_sub(prev.timestamp_ns) {
                Some(delta) if delta > 0 && delta <= rules.thresholds.max_frame_gap_ns => (),
                _ => issue(
                    issues,
                    "TIMESTAMP_GAP_OR_ORDER",
                    Some(f.frame_id),
                    role,
                    true,
                ),
            }
        }
        if !matches!(f.alignment.method.as_str(), "exact_pair" | "registered")
            || f.alignment.reference_frame_id != f.frame_id
            || f.alignment.evidence.trim().is_empty()
            || !f.alignment.error_px.is_finite()
            || f.alignment.error_px < 0.0
            || f.alignment.error_px > rules.thresholds.max_alignment_error_px
        {
            issue(issues, "ALIGNMENT_UNVERIFIED", Some(f.frame_id), role, true);
        }
    }
    Ok(())
}

fn source_ok(s: &Source) -> bool {
    s.kind != SourceKind::Unknown
        && !s.provenance.trim().is_empty()
        && !s.capture_method.trim().is_empty()
        && (s.kind != SourceKind::RealGameCapture
            || [&s.device, &s.package, &s.game_version]
                .iter()
                .all(|x| x.as_ref().is_some_and(|v| !v.trim().is_empty())))
        && (!matches!(
            s.kind,
            SourceKind::HrDownsampled | SourceKind::SyntheticHudComposite
        ) || s.derivation.as_ref().is_some_and(|v| !v.trim().is_empty()))
}

fn verify_ledger(
    r: &Manifest,
    c: &Manifest,
    rb: &Path,
    cb: &Path,
    artifact_hash: Option<&str>,
    issues: &mut Vec<Issue>,
) -> (Option<String>, Option<String>) {
    let (Some(re), Some(ce)) = (&r.pairing_evidence, &c.pairing_evidence) else {
        issue(
            issues,
            "PAIRING_EVIDENCE_MISSING",
            None,
            "alignment declarations alone do not establish frame pairing",
            false,
        );
        return (None, None);
    };
    let read = || -> Result<PairLedger, String> {
        if re.sha256 != ce.sha256 {
            return Err("two arms bind different ledgers".into());
        }
        let bytes = artifact_bytes(rb, re)?;
        artifact_bytes(cb, ce)?;
        let ledger: PairLedger = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        if ledger.schema != LEDGER_SCHEMA
            || ledger.sequence_id != r.sequence_id
            || ledger.timeline_id != r.timeline_id
            || ledger.provenance.trim().is_empty()
            || !matches!(
                ledger.capture_method.as_str(),
                "same_frame_dual_output" | "deterministic_scene_replay"
            )
        {
            return Err("ledger schema, timeline or capture provenance is unsupported".into());
        }
        if ledger.pairs.len() != r.frames.len() || ledger.pairs.len() != c.frames.len() {
            return Err("ledger does not cover both complete sequences".into());
        }
        if artifact_hash != Some(ledger.candidate_artifact_sha256.as_str()) {
            return Err(
                "ledger is not bound to the independently hashed candidate artifact".into(),
            );
        }
        for ((p, rf), cf) in ledger.pairs.iter().zip(&r.frames).zip(&c.frames) {
            if p.frame_id != rf.frame_id
                || p.frame_id != cf.frame_id
                || p.timestamp_ns != rf.timestamp_ns
                || p.candidate_timestamp_ns != cf.timestamp_ns
                || p.reference_sha256 != rf.sha256
                || p.candidate_sha256 != cf.sha256
            {
                return Err(format!(
                    "ledger pair {} does not bind the declared frames",
                    p.frame_id
                ));
            }
        }
        Ok(ledger)
    };
    match read() {
        Ok(l) => (Some(re.sha256.clone()), Some(l.capture_method)),
        Err(e) => {
            issue(issues, "PAIRING_EVIDENCE_INVALID", None, e, false);
            (None, None)
        }
    }
}

fn pixel_mask(w: u32, h: u32, excludes: &[[f64; 4]]) -> Vec<bool> {
    let rects: Vec<[u32; 4]> = excludes
        .iter()
        .map(|r| {
            [
                (r[0] * w as f64).floor() as u32,
                (r[1] * h as f64).floor() as u32,
                (r[2] * w as f64).ceil() as u32,
                (r[3] * h as f64).ceil() as u32,
            ]
        })
        .collect();
    (0..h)
        .flat_map(|y| {
            (0..w).map({
                let rects = &rects;
                move |x| {
                    !rects
                        .iter()
                        .any(|r| r[0] <= x && x < r[2] && r[1] <= y && y < r[3])
                }
            })
        })
        .collect()
}

fn spatial(r: &RgbImage, c: &RgbImage, mask: &[bool]) -> SpatialMetrics {
    let mut sq = 0.0;
    let mut bias = [0.0; 3];
    let mut n = 0u64;
    for ((rp, cp), keep) in r.pixels().zip(c.pixels()).zip(mask) {
        if !keep {
            continue;
        }
        n += 1;
        for k in 0..3 {
            let e = cp[k] as f64 - rp[k] as f64;
            sq += e * e;
            bias[k] += e;
        }
    }
    let mse = sq / (n * 3) as f64;
    for x in &mut bias {
        *x /= n as f64;
    }
    SpatialMetrics {
        psnr_db: (mse > 0.0).then(|| 10.0 * (255.0 * 255.0 / mse).log10()),
        exact_match: mse == 0.0,
        mse,
        channel_bias: bias,
        pixels: n,
    }
}

fn temporal(pr: &RgbImage, pc: &RgbImage, r: &RgbImage, c: &RgbImage, mask: &[bool]) -> (f64, f64) {
    let mut motion = 0.0;
    let mut residual = 0.0;
    let mut n = 0u64;
    for ((((prp, pcp), rp), cp), keep) in pr
        .pixels()
        .zip(pc.pixels())
        .zip(r.pixels())
        .zip(c.pixels())
        .zip(mask)
    {
        if !keep {
            continue;
        }
        n += 3;
        for k in 0..3 {
            motion += (rp[k] as f64 - prp[k] as f64).abs();
            residual += ((cp[k] as f64 - rp[k] as f64) - (pcp[k] as f64 - prp[k] as f64)).abs();
        }
    }
    (motion / n as f64, residual / n as f64)
}

/// Evaluate bound artifacts without writes or device/model access.
pub fn evaluate(reference: &Path, candidate: &Path, rules_path: &Path) -> Result<Report, String> {
    let (rules, rules_hash): (Rules, _) = read_json(rules_path)?;
    validate_rules(&rules)?;
    let (r, r_hash): (Manifest, _) = read_json(reference)?;
    let (c, c_hash): (Manifest, _) = read_json(candidate)?;
    let rb = reference
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let cb = candidate
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut issues = Vec::new();
    validate_manifest(&r, "reference", &rules, &rules_hash, &mut issues)?;
    validate_manifest(&c, "candidate", &rules, &rules_hash, &mut issues)?;
    if r.sequence_id != c.sequence_id
        || r.timeline_id != c.timeline_id
        || r.content_scope != c.content_scope
        || r.color_space != c.color_space
    {
        issue(
            &mut issues,
            "SEQUENCE_CONTEXT_MISMATCH",
            None,
            "sequence/timeline/pixel scope differs",
            true,
        );
    }
    let real = r.source.kind == SourceKind::RealGameCapture
        && c.source.kind == SourceKind::RealGameCapture;
    let source_validity = if !source_ok(&r.source) || !source_ok(&c.source) {
        issue(
            &mut issues,
            "SOURCE_UNKNOWN",
            None,
            "source kind or provenance incomplete",
            false,
        );
        "UNKNOWN"
    } else if real
        && (r.source.device != c.source.device
            || r.source.package != c.source.package
            || r.source.game_version != c.source.game_version)
    {
        issue(
            &mut issues,
            "SOURCE_CONTEXT_MISMATCH",
            None,
            "device/package/game version differs",
            false,
        );
        "MISMATCH"
    } else if real {
        "DECLARED_REAL"
    } else {
        "NON_REAL"
    };
    let artifact_hash = match &c.candidate_artifact {
        Some(binding) => match artifact_bytes(cb, binding) {
            Ok(_) => Some(binding.sha256.clone()),
            Err(e) => {
                issue(&mut issues, "CANDIDATE_ARTIFACT_INVALID", None, e, false);
                None
            }
        },
        None => {
            issue(
                &mut issues,
                "CANDIDATE_ARTIFACT_MISSING",
                None,
                "sequence is not bound to a candidate artifact",
                false,
            );
            None
        }
    };
    let (ledger_hash, ledger_method) =
        verify_ledger(&r, &c, rb, cb, artifact_hash.as_deref(), &mut issues);
    let pairing_verification = if ledger_hash.is_some() {
        "ledger_consistent"
    } else {
        "unknown"
    };
    let mut frames = Vec::new();
    let mut transitions = Vec::new();
    let candidates: BTreeMap<u64, &Frame> = c.frames.iter().map(|f| (f.frame_id, f)).collect();
    let refs: BTreeSet<u64> = r.frames.iter().map(|f| f.frame_id).collect();
    if candidates.len() != c.frames.len() || refs.len() != r.frames.len() {
        issue(
            &mut issues,
            "DUPLICATE_FRAME_ID",
            None,
            "frame ids are not unique",
            true,
        );
    }
    for cf in &c.frames {
        if !refs.contains(&cf.frame_id) {
            issue(
                &mut issues,
                "EXTRA_CANDIDATE_FRAME",
                Some(cf.frame_id),
                "no reference counterpart",
                true,
            );
        }
    }
    let mut previous: Option<(u64, RgbImage, RgbImage)> = None;
    let mut dimensions = None;
    for rf in &r.frames {
        let cf = candidates.get(&rf.frame_id).copied();
        let mut fe = FrameEvidence {
            frame_id: rf.frame_id,
            reference: rf.clone(),
            candidate: cf.cloned(),
            pair_timestamp_delta_ns: cf.map(|x| x.timestamp_ns.abs_diff(rf.timestamp_ns)),
            metrics: None,
            violations: Vec::new(),
        };
        let pair = || -> Result<(RgbImage, RgbImage), String> {
            let cf = cf.ok_or("missing candidate frame")?;
            if cf.timestamp_ns.abs_diff(rf.timestamp_ns)
                > rules.thresholds.max_pair_timestamp_delta_ns
            {
                return Err("pair timestamp exceeds preregistered tolerance".into());
            }
            let ri = load_image(rb, rf)?;
            let ci = load_image(cb, cf)?;
            if ri.dimensions() != ci.dimensions() {
                return Err("reference/candidate dimensions differ (no implicit resize)".into());
            }
            Ok((ri, ci))
        };
        match pair() {
            Err(e) => {
                issue(
                    &mut issues,
                    "FRAME_PAIR_INVALID",
                    Some(rf.frame_id),
                    e,
                    true,
                );
                previous = None;
            }
            Ok((ri, ci)) => {
                if dimensions.is_some_and(|d| d != ri.dimensions()) {
                    issue(
                        &mut issues,
                        "SEQUENCE_DIMENSIONS_CHANGED",
                        Some(rf.frame_id),
                        "fixed comparison space required",
                        true,
                    );
                    previous = None;
                }
                dimensions = Some(ri.dimensions());
                let mask = pixel_mask(ri.width(), ri.height(), &rules.exclude_rects);
                if !mask.iter().any(|x| *x) {
                    issue(
                        &mut issues,
                        "EMPTY_PIXEL_SCOPE",
                        Some(rf.frame_id),
                        "all pixels excluded",
                        true,
                    );
                    previous = None;
                } else {
                    let m = spatial(&ri, &ci, &mask);
                    if m.psnr_db.is_some_and(|v| v < rules.thresholds.min_psnr_db) {
                        fe.violations.push("PSNR_BELOW_FLOOR".into());
                    }
                    if m.channel_bias
                        .iter()
                        .any(|v| v.abs() > rules.thresholds.max_channel_bias)
                    {
                        fe.violations.push("CHANNEL_BIAS_ABOVE_LIMIT".into());
                    }
                    fe.metrics = Some(m);
                    if let Some((prev_id, pr, pc)) = &previous {
                        if prev_id.checked_add(1) == Some(rf.frame_id)
                            && pr.dimensions() == ri.dimensions()
                        {
                            let (motion, residual) = temporal(pr, pc, &ri, &ci, &mask);
                            let mut violations = Vec::new();
                            if residual > rules.thresholds.max_temporal_residual_mae {
                                violations.push("TEMPORAL_RESIDUAL_ABOVE_LIMIT".into());
                            }
                            if motion > rules.thresholds.max_reference_motion_mae {
                                issue(
                                    &mut issues,
                                    "MOTION_OR_PAIRING_OUT_OF_RANGE",
                                    Some(rf.frame_id),
                                    format!(
                                        "reference change MAE {motion} exceeds detector coverage"
                                    ),
                                    true,
                                );
                            }
                            transitions.push(TransitionEvidence {
                                previous_frame_id: *prev_id,
                                frame_id: rf.frame_id,
                                reference_motion_mae: motion,
                                temporal_residual_mae: residual,
                                violations,
                            });
                        }
                    }
                    previous = Some((rf.frame_id, ri, ci));
                }
            }
        }
        frames.push(fe);
    }
    if r.frames.len() < rules.thresholds.min_frames {
        issue(
            &mut issues,
            "INSUFFICIENT_FRAMES",
            None,
            "sequence shorter than preregistered minimum",
            true,
        );
    }
    if transitions.len() != r.frames.len().saturating_sub(1) {
        issue(
            &mut issues,
            "INCOMPLETE_TEMPORAL_COVERAGE",
            None,
            "not all adjacent reference transitions were measurable",
            true,
        );
    }
    if !transitions
        .iter()
        .any(|t| t.reference_motion_mae >= rules.thresholds.min_reference_motion_mae)
    {
        issue(
            &mut issues,
            "INSUFFICIENT_MOTION_COVERAGE",
            None,
            "static sequence cannot qualify temporal quality",
            true,
        );
    }
    let failed = frames.iter().any(|f| !f.violations.is_empty())
        || transitions.iter().any(|t| !t.violations.is_empty());
    let metrics_verdict = if issues.iter().any(|i| i.invalidates_measurement) {
        "UNKNOWN"
    } else if failed {
        "FAIL"
    } else {
        "PASS"
    };
    let verdict = if !issues.is_empty() {
        "UNKNOWN"
    } else {
        metrics_verdict
    };
    Ok(Report {
        schema: "phonefarm/sequence_quality_report@1".into(), verdict: verdict.into(), metrics_verdict: metrics_verdict.into(),
        source_validity: source_validity.into(), pairing_verification: pairing_verification.into(),
        acquisition_attestation: "not_checked".into(), real_game_qualification: false, real_game_source_pair: false,
        declared_real_game_source_pair: source_validity == "DECLARED_REAL" && ledger_hash.is_some() && ledger_method.as_deref() == Some("same_frame_dual_output"),
        artifact_bound: ledger_hash.is_some() && artifact_hash.is_some(), candidate_artifact: c.candidate_artifact,
        pairing_capture_method: ledger_method, no_ghosting_proven: false,
        scope: "Listed paired frames, frozen pixel mask, RGB PSNR/channel bias and adjacent residual change only".into(),
        limitations: vec!["No optical flow, occlusion model, perceptual metric or complete ghosting detector".into(), "Source/alignment/ledger declarations need capture-system or human provenance review; hashes are not attestations".into(), "No performance, power, deployment or cross-game conclusion; synthetic/derived inputs are not real-game acceptance".into()],
        protocol_id: rules.protocol_id.clone(), sequence_id: r.sequence_id.clone(), content_scope: r.content_scope.clone(),
        reference_source: r.source, candidate_source: c.source, rules_sha256: rules_hash,
        reference_manifest_sha256: r_hash, candidate_manifest_sha256: c_hash, pairing_evidence_sha256: ledger_hash,
        rules, issues, frames, transitions,
    })
}

/// Public CLI entry, also used by integration tests. No device or model calls.
pub fn run_sequence_quality(args: &[String]) -> i32 {
    let mut paths = BTreeMap::new();
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--json" => (),
            "--help" | "-h" => {
                println!("{USAGE}");
                return 0;
            }
            "--reference" | "--candidate" | "--rules" | "--out" => {
                let Some(value) = it.next() else {
                    eprintln!("missing {arg} value\n{USAGE}");
                    return 2;
                };
                if value.starts_with("--") || paths.insert(arg.as_str(), value.as_str()).is_some() {
                    eprintln!("invalid or repeated {arg}\n{USAGE}");
                    return 2;
                }
            }
            _ => {
                eprintln!("unknown argument {arg}\n{USAGE}");
                return 2;
            }
        }
    }
    let (Some(r), Some(c), Some(rules)) = (
        paths.get("--reference"),
        paths.get("--candidate"),
        paths.get("--rules"),
    ) else {
        eprintln!("{USAGE}");
        return 2;
    };
    match evaluate(Path::new(r), Path::new(c), Path::new(rules)) {
        Err(e) => {
            eprintln!("sequence-quality input error: {e}");
            2
        }
        Ok(report) => {
            let json =
                serde_json::to_string_pretty(&report).expect("finite quality metrics") + "\n";
            if let Some(out) = paths.get("--out") {
                let p = Path::new(out);
                let write = || -> Result<(), String> {
                    if let Some(parent) = p.parent().filter(|x| !x.as_os_str().is_empty()) {
                        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
                    }
                    if p.exists() {
                        if std::fs::read_to_string(p).map_err(|e| e.to_string())? == json {
                            return Ok(());
                        }
                        return Err("report already exists with different contents; use a new evidence path".into());
                    }
                    use std::io::Write;
                    let mut file = std::fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(p)
                        .map_err(|e| e.to_string())?;
                    file.write_all(json.as_bytes())
                        .and_then(|_| file.sync_all())
                        .map_err(|e| e.to_string())
                };
                if let Err(e) = write() {
                    eprintln!("cannot archive report: {e}");
                    return 2;
                }
            }
            print!("{json}");
            match report.verdict.as_str() {
                "PASS" => 0,
                "FAIL" => 1,
                _ => 3,
            }
        }
    }
}
