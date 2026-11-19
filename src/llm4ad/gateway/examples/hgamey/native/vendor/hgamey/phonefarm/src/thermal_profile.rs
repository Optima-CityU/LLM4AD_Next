//! Receiver-owned typed temperature policy. No device operations or performance verdicts.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

type ThermalResult<T> = Result<T, String>;
const MAX_SENSORS: usize = 128;
const MAX_SAMPLES: usize = 512;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum Domain {
    Cpu,
    Gpu,
    Skin,
    Battery,
    Unknown,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Unit {
    MilliCelsius,
    DeciCelsius,
    Celsius,
    Unknown,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TripPoint {
    pub kind: String,
    pub raw: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SensorMetadata {
    pub path: String,
    pub sensor_type: String,
    pub domain: Domain,
    pub unit: Unit,
    pub trip_points: Vec<TripPoint>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Sample {
    pub uptime_s: f64,
    pub readings: BTreeMap<String, f64>,
    #[serde(default)]
    pub thermal_status: Option<u8>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProfileSensor {
    pub path: String,
    pub sensor_type: String,
    pub domain: Domain,
    pub unit: Unit,
    pub admission_cap_c: f64,
    pub operating_cap_c: f64,
    pub cap_basis: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ExcludedSensor {
    pub path: String,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct WindowRules {
    pub warmup_min_seconds: f64,
    pub warmup_min_samples: usize,
    pub sample_max_gap_seconds: f64,
    pub warmup_max_range_c: f64,
    pub reference_tolerance_c: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    pub schema: String,
    pub id: String,
    pub metadata: Vec<SensorMetadata>,
    pub sensors: Vec<ProfileSensor>,
    pub excluded: Vec<ExcludedSensor>,
    pub window: WindowRules,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Assessment {
    pub scope: String,
    pub compliant: bool,
    pub reasons: Vec<String>,
    pub normalized_c: BTreeMap<String, f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct WindowAssessment {
    pub scope: String,
    pub compliant: bool,
    pub reasons: Vec<String>,
    pub samples: Vec<Assessment>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Band {
    pub low_c: f64,
    pub high_c: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ReferenceBand {
    pub schema: String,
    pub profile_sha256: String,
    pub bands: BTreeMap<String, Band>,
    pub warmup: Vec<Sample>,
}

fn normalize(raw: f64, unit: &Unit) -> ThermalResult<f64> {
    if !raw.is_finite() {
        return Err("nonfinite raw temperature".into());
    }
    let c = match unit {
        Unit::MilliCelsius => raw / 1000.0,
        Unit::DeciCelsius => raw / 10.0,
        Unit::Celsius => raw,
        Unit::Unknown => return Err("unknown temperature unit".into()),
    };
    if !(-50.0..=150.0).contains(&c) {
        return Err("temperature outside declared physical range".into());
    }
    Ok(c)
}

fn type_matches(s: &SensorMetadata) -> bool {
    let t = s.sensor_type.to_ascii_lowercase();
    match s.domain {
        Domain::Cpu => t.starts_with("cpu-") || t.starts_with("cpullc"),
        Domain::Gpu => t.starts_with("gpuss") || t.starts_with("gpu-"),
        Domain::Skin => {
            t.starts_with("skin-") || t.starts_with("shell_") || t.starts_with("shell-")
        }
        Domain::Battery => t == "battery",
        Domain::Unknown => true,
    }
}

pub fn resolve(id: &str, metadata: &[SensorMetadata]) -> ThermalResult<Profile> {
    let loaded = match id {
        "legacy_cold_v1" => false,
        "loaded_steady_v1" | "capability_absolute_v1" => true,
        _ => return Err("unknown receiver temperature profile".into()),
    };
    if metadata.is_empty() || metadata.len() > MAX_SENSORS {
        return Err("metadata sensor count outside bounds".into());
    }
    let mut meta = metadata.to_vec();
    meta.sort_by(|a, b| a.path.cmp(&b.path));
    let mut seen = BTreeSet::new();
    let mut domains = BTreeSet::new();
    let mut sensors = Vec::new();
    let mut excluded = Vec::new();
    for s in &meta {
        if !s.path.starts_with("/sys/")
            || s.path.len() > 512
            || s.path.contains("..")
            || s.path.chars().any(|c| c.is_control())
            || s.sensor_type.is_empty()
            || s.sensor_type.len() > 128
            || !seen.insert(s.path.clone())
            || s.trip_points.len() > 16
        {
            return Err("invalid or duplicated sensor metadata".into());
        }
        if !type_matches(s) {
            return Err(format!("domain/type mismatch: {}", s.path));
        }
        if s.trip_points
            .iter()
            .any(|t| !t.raw.is_finite() || t.kind.len() > 32)
        {
            return Err("invalid trip metadata".into());
        }
        if s.domain == Domain::Unknown
            || (!loaded && !matches!(s.domain, Domain::Cpu | Domain::Gpu))
        {
            excluded.push(ExcludedSensor {
                path: s.path.clone(),
                reason: "not a selected temperature domain for this preset".into(),
            });
            continue;
        }
        if s.unit == Unit::Unknown {
            return Err(format!("unknown selected sensor unit: {}", s.path));
        }
        let (admission, operating, basis) = if !loaded {
            (
                40.0,
                70.0,
                "receiver legacy SoC cold/operating caps".to_string(),
            )
        } else if s.domain == Domain::Battery {
            (
                45.0,
                45.0,
                "receiver battery cap; not a SoC limit".to_string(),
            )
        } else {
            let mut passive = Vec::new();
            let mut hal_severe = Vec::new();
            for t in &s.trip_points {
                let c = normalize(t.raw, &s.unit)?;
                if t.kind == "passive" || t.kind == "hal_severe" {
                    if c <= 20.0 {
                        return Err("incoherent passive trip".into());
                    }
                    if t.kind == "passive" {
                        passive.push(c);
                    } else {
                        hal_severe.push(c);
                    }
                }
            }
            if passive.is_empty() {
                if s.domain != Domain::Skin {
                    return Err(format!(
                        "missing passive threshold for selected CPU/GPU sensor: {}",
                        s.path
                    ));
                }
                excluded.push(ExcludedSensor {
                    path: s.path.clone(),
                    reason: "no valid passive trip; diagnostic only".into(),
                });
                continue;
            }
            if hal_severe.is_empty() {
                return Err(format!("missing matched HAL severe threshold: {}", s.path));
            }
            let margin = if s.domain == Domain::Skin { 3.0 } else { 5.0 };
            let minimum = passive
                .into_iter()
                .chain(hal_severe)
                .fold(f64::INFINITY, f64::min);
            let cap = minimum - margin;
            if cap <= 20.0 {
                return Err("passive trip cannot form receiver ceiling".into());
            }
            (cap,cap,format!("minimum passive/HAL severe threshold {minimum}C minus receiver margin {margin}C"))
        };
        domains.insert(s.domain.clone());
        sensors.push(ProfileSensor {
            path: s.path.clone(),
            sensor_type: s.sensor_type.clone(),
            domain: s.domain.clone(),
            unit: s.unit.clone(),
            admission_cap_c: admission,
            operating_cap_c: operating,
            cap_basis: basis,
        });
    }
    let required = if loaded {
        vec![Domain::Cpu, Domain::Gpu, Domain::Skin, Domain::Battery]
    } else {
        vec![Domain::Cpu, Domain::Gpu]
    };
    if required.iter().any(|d| !domains.contains(d)) {
        return Err("missing selected temperature domain".into());
    }
    Ok(Profile {
        schema: "phonefarm/thermal_profile@1".into(),
        id: id.into(),
        metadata: meta,
        sensors,
        excluded,
        window: WindowRules {
            warmup_min_seconds: 30.0,
            warmup_min_samples: 6,
            sample_max_gap_seconds: 6.0,
            warmup_max_range_c: 3.0,
            reference_tolerance_c: 2.0,
        },
    })
}

fn validate(profile: &Profile) -> ThermalResult<()> {
    if resolve(&profile.id, &profile.metadata)? != *profile {
        return Err("serialized profile differs from receiver preset".into());
    }
    Ok(())
}

pub fn profile_sha256(profile: &Profile) -> ThermalResult<String> {
    validate(profile)?;
    let bytes = serde_json::to_vec(profile).map_err(|e| e.to_string())?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

fn assess(profile: &Profile, sample: &Sample, admission: bool) -> ThermalResult<Assessment> {
    validate(profile)?;
    if !sample.uptime_s.is_finite() || sample.uptime_s < 0.0 {
        return Err("invalid sample uptime".into());
    }
    let expected: BTreeSet<_> = profile.sensors.iter().map(|s| s.path.clone()).collect();
    let actual: BTreeSet<_> = sample.readings.keys().cloned().collect();
    let mut reasons = Vec::new();
    if actual != expected {
        reasons.push("missing or unexpected sensor reading paths".into());
    }
    if profile.id != "legacy_cold_v1" && !matches!(sample.thermal_status, Some(0..=2)) {
        reasons.push("Android thermal status unknown or severe".into());
    }
    let mut normalized = BTreeMap::new();
    for s in &profile.sensors {
        if let Some(raw) = sample.readings.get(&s.path) {
            let c = normalize(*raw, &s.unit)?;
            let cap = if admission {
                s.admission_cap_c
            } else {
                s.operating_cap_c
            };
            let bad = if admission && profile.id == "legacy_cold_v1" {
                c >= cap
            } else {
                c > cap
            };
            if bad {
                reasons.push(format!(
                    "{} exceeds {}C {} ceiling",
                    s.path,
                    cap,
                    if admission { "admission" } else { "operating" }
                ));
            }
            normalized.insert(s.path.clone(), c);
        }
    }
    Ok(Assessment {
        scope: "temperature_snapshot_only".into(),
        compliant: reasons.is_empty(),
        reasons,
        normalized_c: normalized,
    })
}

pub fn evaluate_snapshot(profile: &Profile, sample: &Sample) -> ThermalResult<Assessment> {
    assess(profile, sample, false)
}
pub fn evaluate_admission_snapshot(
    profile: &Profile,
    sample: &Sample,
) -> ThermalResult<Assessment> {
    assess(profile, sample, true)
}

pub fn evaluate_window(profile: &Profile, samples: &[Sample]) -> ThermalResult<WindowAssessment> {
    validate(profile)?;
    if samples.len() < 2 || samples.len() > MAX_SAMPLES {
        return Err("window sample count outside bounds".into());
    }
    let mut reasons = Vec::new();
    let mut reports = Vec::new();
    for (i, sample) in samples.iter().enumerate() {
        let report = evaluate_snapshot(profile, sample)?;
        if !report.compliant {
            reasons.push(format!("sample {i} is not temperature compliant"));
        }
        if i > 0 {
            let gap = sample.uptime_s - samples[i - 1].uptime_s;
            if gap <= 0.0 || gap > profile.window.sample_max_gap_seconds {
                reasons.push(format!("sample {i} clock/order/gap invalid"));
            }
        }
        reports.push(report);
    }
    Ok(WindowAssessment {
        scope: "provided_temperature_readings_only".into(),
        compliant: reasons.is_empty(),
        reasons,
        samples: reports,
    })
}

pub fn freeze_reference_band(profile: &Profile, warmup: &[Sample]) -> ThermalResult<ReferenceBand> {
    if profile.id == "capability_absolute_v1" {
        return Err(
            "control capability diagnostics cannot establish a performance reference".into(),
        );
    }
    freeze_band(profile, warmup, true)
}

pub fn freeze_capability_diagnostics(
    profile: &Profile,
    warmup: &[Sample],
) -> ThermalResult<ReferenceBand> {
    if profile.id != "capability_absolute_v1" {
        return Err("absolute capability diagnostics require their explicit preset".into());
    }
    freeze_band(profile, warmup, false)
}

fn freeze_band(profile: &Profile, warmup: &[Sample], stable: bool) -> ThermalResult<ReferenceBand> {
    let report = evaluate_window(profile, warmup)?;
    if !report.compliant
        || warmup.len() < profile.window.warmup_min_samples
        || warmup.last().unwrap().uptime_s - warmup[0].uptime_s < profile.window.warmup_min_seconds
    {
        return Err("warmup incomplete, noncompliant or too short".into());
    }
    let mut bands = BTreeMap::new();
    for s in &profile.sensors {
        let values: Vec<_> = report
            .samples
            .iter()
            .map(|r| r.normalized_c[&s.path])
            .collect();
        let lo = values.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if stable && hi - lo > profile.window.warmup_max_range_c {
            return Err(format!("warmup is not thermally stable: {}", s.path));
        }
        if profile.id == "legacy_cold_v1" && hi >= s.admission_cap_c {
            return Err("legacy warmup has not cooled below admission ceiling".into());
        }
        let mean = values.iter().sum::<f64>() / values.len() as f64;
        bands.insert(
            s.path.clone(),
            Band {
                low_c: mean - profile.window.reference_tolerance_c,
                high_c: (mean + profile.window.reference_tolerance_c).min(s.operating_cap_c),
            },
        );
    }
    Ok(ReferenceBand {
        schema: if stable {
            "phonefarm/thermal_reference@1"
        } else {
            "phonefarm/thermal_capability_diagnostics@1"
        }
        .into(),
        profile_sha256: profile_sha256(profile)?,
        bands,
        warmup: warmup.to_vec(),
    })
}

pub fn evaluate_against_reference(
    profile: &Profile,
    reference: &ReferenceBand,
    samples: &[Sample],
) -> ThermalResult<WindowAssessment> {
    if freeze_reference_band(profile, &reference.warmup)? != *reference {
        return Err("reference differs from frozen receiver warmup/profile".into());
    }
    let mut report = evaluate_window(profile, samples)?;
    for (i, sample) in report.samples.iter().enumerate() {
        for (path, c) in &sample.normalized_c {
            let band = &reference.bands[path];
            if *c < band.low_c || *c > band.high_c {
                report
                    .reasons
                    .push(format!("sample {i}: {path} outside frozen reference band"));
            }
        }
    }
    report.compliant = report.reasons.is_empty();
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn metadata() -> Vec<SensorMetadata> {
        [
            ("cpu-2-1-1", Domain::Cpu, 108.0),
            ("gpuss-4", Domain::Gpu, 95.0),
            ("skin-msm-therm", Domain::Skin, 60.0),
            ("battery", Domain::Battery, 0.0),
        ]
        .into_iter()
        .enumerate()
        .map(|(i, (t, d, trip))| SensorMetadata {
            path: format!("/sys/class/thermal/thermal_zone{i}/temp"),
            sensor_type: t.into(),
            domain: d.clone(),
            unit: Unit::MilliCelsius,
            trip_points: if trip > 0.0 {
                vec![
                    TripPoint {
                        kind: "passive".into(),
                        raw: trip * 1000.0,
                    },
                    TripPoint {
                        kind: "hal_severe".into(),
                        raw: (if d == Domain::Skin { 60.0 } else { 95.0 }) * 1000.0,
                    },
                ]
            } else {
                vec![]
            },
        })
        .collect()
    }
    fn sample(p: &Profile, t: f64, cpu: f64) -> Sample {
        Sample {
            uptime_s: t,
            thermal_status: Some(0),
            readings: p
                .sensors
                .iter()
                .map(|s| {
                    (
                        s.path.clone(),
                        match s.domain {
                            Domain::Cpu => cpu,
                            Domain::Gpu => 60.0,
                            Domain::Skin => 50.0,
                            Domain::Battery => 39.0,
                            Domain::Unknown => unreachable!(),
                        } * 1000.0,
                    )
                })
                .collect(),
        }
    }
    fn warmup(p: &Profile) -> Vec<Sample> {
        (0..=6).map(|i| sample(p, i as f64 * 5.0, 80.0)).collect()
    }
    #[test]
    fn loaded_soc80_is_not_battery80_or_legacy_cold() {
        let p = resolve("loaded_steady_v1", &metadata()).unwrap();
        assert!(
            evaluate_admission_snapshot(&p, &sample(&p, 0.0, 80.0))
                .unwrap()
                .compliant
        );
        let old = resolve("legacy_cold_v1", &metadata()).unwrap();
        assert!(
            !evaluate_admission_snapshot(&old, &sample(&old, 0.0, 80.0))
                .unwrap()
                .compliant
        );
        assert!(
            !evaluate_snapshot(&old, &sample(&old, 0.0, 80.0))
                .unwrap()
                .compliant
        );
        let mut s = sample(&p, 0.0, 80.0);
        s.readings.insert(
            p.sensors
                .iter()
                .find(|s| s.domain == Domain::Battery)
                .unwrap()
                .path
                .clone(),
            46000.0,
        );
        assert!(!evaluate_snapshot(&p, &s).unwrap().compliant);
    }
    #[test]
    fn typed_units_and_unknown_domain_are_preserved() {
        let mut m = metadata();
        m[3].unit = Unit::DeciCelsius;
        m.push(SensorMetadata {
            path: "/sys/class/thermal/socd/temp".into(),
            sensor_type: "socd".into(),
            domain: Domain::Unknown,
            unit: Unit::Unknown,
            trip_points: vec![TripPoint {
                kind: "passive".into(),
                raw: 90.0,
            }],
        });
        let p = resolve("loaded_steady_v1", &m).unwrap();
        let mut s = sample(&p, 0.0, 80.0);
        s.readings.insert(m[3].path.clone(), 393.0);
        assert_eq!(
            evaluate_snapshot(&p, &s).unwrap().normalized_c[&m[3].path],
            39.3
        );
        assert_eq!(p.excluded.len(), 1);
        m[0].unit = Unit::Unknown;
        assert!(resolve("loaded_steady_v1", &m).is_err());
        m[0].unit = Unit::MilliCelsius;
        m[0].domain = Domain::Battery;
        assert!(resolve("loaded_steady_v1", &m).is_err());
    }
    #[test]
    fn missing_domain_unknown_preset_duplicate_path_refuse() {
        let m = metadata();
        assert!(resolve("caller_hot", &m).is_err());
        assert!(resolve("loaded_steady_v1", &m[..3]).is_err());
        let mut d = m.clone();
        d.push(m[0].clone());
        assert!(resolve("loaded_steady_v1", &d).is_err());
        let mut d = m;
        d[2].trip_points.clear();
        assert!(resolve("loaded_steady_v1", &d).is_err());
    }
    #[test]
    fn no_trip_shell_can_be_diagnostic_without_losing_skin() {
        let mut m = metadata();
        m.push(SensorMetadata {
            path: "/sys/class/thermal/shell/temp".into(),
            sensor_type: "shell_front".into(),
            domain: Domain::Skin,
            unit: Unit::MilliCelsius,
            trip_points: vec![],
        });
        let p = resolve("loaded_steady_v1", &m).unwrap();
        assert_eq!(p.sensors.len(), 4);
        assert_eq!(p.excluded.len(), 1);
    }
    #[test]
    fn unbounded_cpu_or_gpu_cannot_be_excluded_because_a_sibling_has_trips() {
        for domain in [Domain::Cpu, Domain::Gpu] {
            let mut m = metadata();
            let mut sibling = m.iter().find(|s| s.domain == domain).unwrap().clone();
            sibling.path = "/sys/class/thermal/thermal_zone100/temp".into();
            sibling.trip_points.retain(|t| t.kind != "passive");
            m.push(sibling);
            assert!(resolve("loaded_steady_v1", &m).is_err());
        }
    }
    #[test]
    fn raw_missing_extra_nonfinite_and_severity_failclosed() {
        let p = resolve("loaded_steady_v1", &metadata()).unwrap();
        let mut s = sample(&p, 0.0, 80.0);
        s.thermal_status = None;
        assert!(!evaluate_snapshot(&p, &s).unwrap().compliant);
        s.thermal_status = Some(3);
        assert!(!evaluate_snapshot(&p, &s).unwrap().compliant);
        s.thermal_status = Some(0);
        s.readings.remove(&p.sensors[0].path);
        assert!(!evaluate_snapshot(&p, &s).unwrap().compliant);
        s = sample(&p, 0.0, 80.0);
        s.readings.insert("/sys/extra".into(), 0.0);
        assert!(!evaluate_snapshot(&p, &s).unwrap().compliant);
        s = sample(&p, 0.0, 80.0);
        s.readings.insert(p.sensors[0].path.clone(), f64::NAN);
        assert!(evaluate_snapshot(&p, &s).is_err());
    }
    #[test]
    fn passive_cap_and_profile_tamper_refuse() {
        let mut p = resolve("loaded_steady_v1", &metadata()).unwrap();
        assert_eq!(p.sensors[0].operating_cap_c, 90.0);
        assert!(
            !evaluate_snapshot(&p, &sample(&p, 0.0, 91.0))
                .unwrap()
                .compliant
        );
        p.sensors[0].operating_cap_c = 140.0;
        assert!(evaluate_snapshot(&p, &sample(&p, 0.0, 104.0)).is_err());
    }
    #[test]
    fn warmup_stability_and_clock_are_not_inferred_from_one_point() {
        let p = resolve("loaded_steady_v1", &metadata()).unwrap();
        let mut w = warmup(&p);
        assert!(freeze_reference_band(&p, &w).is_ok());
        assert!(freeze_reference_band(&p, &w[..5]).is_err());
        w[2].uptime_s = w[1].uptime_s;
        assert!(freeze_reference_band(&p, &w).is_err());
        w = warmup(&p);
        w[6].readings.insert(p.sensors[0].path.clone(), 84000.0);
        assert!(freeze_reference_band(&p, &w).is_err());
        w = warmup(&p);
        w[2].uptime_s = 17.0;
        assert!(freeze_reference_band(&p, &w).is_err());
    }
    #[test]
    fn short_runtime_uses_frozen_band_without_requiring_30s() {
        let p = resolve("loaded_steady_v1", &metadata()).unwrap();
        let r = freeze_reference_band(&p, &warmup(&p)).unwrap();
        let runtime = vec![sample(&p, 40.0, 80.0), sample(&p, 42.0, 81.0)];
        assert!(
            evaluate_against_reference(&p, &r, &runtime)
                .unwrap()
                .compliant
        );
        let hot = vec![sample(&p, 40.0, 83.0), sample(&p, 42.0, 83.0)];
        assert!(!evaluate_against_reference(&p, &r, &hot).unwrap().compliant);
    }
    #[test]
    fn rehashed_or_widened_reference_is_not_a_new_policy() {
        let p = resolve("loaded_steady_v1", &metadata()).unwrap();
        let mut r = freeze_reference_band(&p, &warmup(&p)).unwrap();
        r.bands.get_mut(&p.sensors[0].path).unwrap().high_c = 100.0;
        assert!(evaluate_against_reference(
            &p,
            &r,
            &[sample(&p, 40.0, 95.0), sample(&p, 42.0, 95.0)]
        )
        .is_err());
        r = freeze_reference_band(&p, &warmup(&p)).unwrap();
        r.profile_sha256 = "00".repeat(32);
        assert!(evaluate_against_reference(
            &p,
            &r,
            &[sample(&p, 40.0, 80.0), sample(&p, 42.0, 80.0)]
        )
        .is_err());
    }
    #[test]
    fn serialized_unknown_fields_and_profile_preservation() {
        let p = resolve("loaded_steady_v1", &metadata()).unwrap();
        let mut v = serde_json::to_value(&p).unwrap();
        v["caller_cap"] = serde_json::json!(999);
        assert!(serde_json::from_value::<Profile>(v).is_err());
        let frozen = serde_json::to_vec(&p).unwrap();
        let q: Profile = serde_json::from_slice(&frozen).unwrap();
        assert_eq!(profile_sha256(&p).unwrap(), profile_sha256(&q).unwrap());
    }
    #[test]
    fn capability_absolute_retains_caps_integrity_but_not_performance_stability() {
        let cap = resolve("capability_absolute_v1", &metadata()).unwrap();
        let loaded = resolve("loaded_steady_v1", &metadata()).unwrap();
        let mut w = warmup(&cap);
        let path = cap
            .sensors
            .iter()
            .find(|s| s.domain == Domain::Cpu)
            .unwrap()
            .path
            .clone();
        w[1].readings.insert(path.clone(), 83100.);
        assert!(freeze_reference_band(&loaded, &w).is_err());
        let d = freeze_capability_diagnostics(&cap, &w).unwrap();
        assert_eq!(d.schema, "phonefarm/thermal_capability_diagnostics@1");
        assert!(freeze_reference_band(&cap, &w).is_err());
        assert!(evaluate_against_reference(&cap, &d, &w).is_err());
        assert!(freeze_capability_diagnostics(&loaded, &w).is_err());
        assert!(freeze_capability_diagnostics(&cap, &w[..5]).is_err());
        w[2].thermal_status = Some(3);
        assert!(freeze_capability_diagnostics(&cap, &w).is_err());
        w[2].thermal_status = Some(2);
        w[2].readings.insert(path.clone(), 120000.);
        assert!(freeze_capability_diagnostics(&cap, &w).is_err());
        w[2].readings.remove(&path);
        assert!(freeze_capability_diagnostics(&cap, &w).is_err());
        let mut m = metadata();
        m[0].unit = Unit::Unknown;
        assert!(resolve("capability_absolute_v1", &m).is_err());
    }
}
