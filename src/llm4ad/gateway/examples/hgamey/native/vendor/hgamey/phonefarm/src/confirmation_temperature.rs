//! Bounded read-only thermal producer. Uses only receiver-generated frozen paths.
use super::*;
const METADATA: &str = include_str!("../loop_v1/tools/thermal_profile_metadata.sh");
const REJECTION_GATE: &str = include_str!("../loop_v1/tools/thermal_rejection_gate.awk");
#[cfg(test)]
#[path = "tests/support/thermal_rejection_diagnostics.rs"]
mod rejection_diagnostic_tests;

pub(super) fn parse_metadata(raw: &str, boot: &str) -> Result<Value, String> {
    require(
        raw.starts_with("#phonefarm_thermal_metadata v1\n") && raw.lines().last() == Some("#end"),
        "thermal inventory truncated",
    )?;
    let mut sensors = BTreeMap::<String, Value>::new();
    let mut trips = Vec::new();
    let mut found_boot = false;
    let mut excluded = Vec::new();
    for line in raw.lines().skip(1) {
        if line == "#end" {
            continue;
        }
        let f = line.split('\t').collect::<Vec<_>>();
        match f.as_slice() {
            ["BOOT", b] if !found_boot && *b == boot => found_boot = true,
            ["SENSOR", path, ty, domain, unit] => {
                require(
                    sensor_path(path)
                        && !ty.is_empty()
                        && ty.len() <= 128
                        && ty
                            .bytes()
                            .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c)),
                    "unsafe thermal sensor identity",
                )?;
                require(
                    matches!(*domain, "cpu" | "gpu" | "skin" | "battery")
                        && matches!(*unit, "milli_celsius" | "deci_celsius"),
                    "unknown thermal domain/unit",
                )?;
                require(
                    !sensors.contains_key(*path),
                    "duplicate thermal sensor path",
                )?;
                sensors.insert((*path).into(),json!({"path":path,"sensor_type":ty,"domain":domain,"unit":unit,"trip_points":[]}));
            }
            ["TRIP", path, kind, value] => {
                let value = value
                    .parse::<f64>()
                    .map_err(|_| "thermal trip raw invalid")?;
                require(
                    value.is_finite()
                        && value > 0.
                        && matches!(*kind, "passive" | "active" | "critical" | "hot"),
                    "unknown thermal trip value/type",
                )?;
                trips.push(((*path).to_owned(), json!({"kind":kind,"raw":value})));
            }
            ["EXCLUDED", path, ty, reason]
                if sensor_path(path) && *reason == "no_passive_trip_contract" =>
            {
                excluded.push(json!({"path":path,"sensor_type":ty,"reason":reason}))
            }
            _ => return Err("malformed/unknown thermal inventory record".into()),
        }
    }
    require(found_boot, "thermal inventory boot missing/changed")?;
    for (path, trip) in trips {
        sensors.get_mut(&path).ok_or("thermal trip has no sensor")?["trip_points"]
            .as_array_mut()
            .ok_or("trip list")?
            .push(trip);
    }
    require(
        !sensors.is_empty() && sensors.len() <= 128,
        "thermal sensor count invalid",
    )?;
    Ok(
        json!({"schema":"phonefarm/raw_thermal_inventory@1","boot_id":boot,"sensors":sensors.into_values().collect::<Vec<_>>(),"excluded":excluded}),
    )
}
fn sensor_path(path: &str) -> bool {
    path == "/sys/class/power_supply/battery/temp"
        || path
            .strip_prefix("/sys/class/thermal/thermal_zone")
            .and_then(|s| s.strip_suffix("/temp"))
            .is_some_and(|n| !n.is_empty() && n.len() <= 4 && n.bytes().all(|c| c.is_ascii_digit()))
}
pub(super) fn collect_metadata(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    boot: &str,
) -> Result<Value, String> {
    save(dir, "thermal_metadata.sh", METADATA.as_bytes())?;
    t.input_hashes
        .insert(dir.join("thermal_metadata.sh"), sha(METADATA.as_bytes()));
    let raw = checked(
        t,
        "thermal_metadata",
        "sh",
        Some(&dir.join("thermal_metadata.sh")),
        15,
        256 * 1024,
        lease,
    )?;
    let raw_ref = save(dir, "thermal_metadata.raw.tsv", &raw)?;
    let parsed = parse_metadata(std::str::from_utf8(&raw).map_err(|e| e.to_string())?, boot)?;
    let parsed = json!({"raw":raw_ref,"inventory":parsed});
    save_json(dir, "thermal_inventory.json", &parsed)?;
    Ok(parsed)
}
pub(super) fn snapshot_script(paths: &[String]) -> Result<String, String> {
    require(
        !paths.is_empty() && paths.len() <= 128 && paths.iter().all(|p| sensor_path(p)),
        "invalid receiver thermal collection paths",
    )?;
    require(
        paths.iter().collect::<BTreeSet<_>>().len() == paths.len(),
        "duplicate thermal collection paths",
    )?;
    let mut s=String::from("#!/system/bin/sh\nset -eu\nprintf '#phonefarm_thermal_sample v1\\n'\nprintf 'UPTIME\\t%s\\n' \"$(cut -d' ' -f1 /proc/uptime)\"\n");
    // One cat process reads the fixed ordered sensor set; no repeated whole-/proc enumeration.
    s += &format!(
        "values=$(cat {})\nset -- $values\n[ \"$#\" -eq {} ] || exit 81\n",
        paths.iter().map(|p| quote(p)).collect::<Vec<_>>().join(" "),
        paths.len()
    );
    for p in paths {
        s += &format!("printf 'TEMP\\t%s\\t%s\\n' {} \"$1\"; shift\n", quote(p));
    }
    s += "printf '#hal_begin\\n'\ntimeout 8 dumpsys thermalservice\nprintf '#hal_end\\n'\nprintf '#end\\n'\n";
    Ok(s)
}
pub(super) fn parse_sample(raw: &str, paths: &[String]) -> Result<Value, String> {
    require(
        raw.starts_with("#phonefarm_thermal_sample v1\n") && raw.lines().last() == Some("#end"),
        "thermal sample truncated",
    )?;
    let hal = if let Some((_, tail)) = raw.split_once("#hal_begin\n") {
        let (body, _) = tail
            .split_once("#hal_end\n")
            .ok_or("thermal HAL receipt truncated")?;
        Some(hal_status(body)?)
    } else {
        None
    };
    let thermal_part = raw
        .split_once("#hal_begin\n")
        .map(|(a, _)| a)
        .unwrap_or(raw);
    let mut up = None;
    let mut values = BTreeMap::new();
    for line in thermal_part.lines().skip(1) {
        if line == "#end" {
            continue;
        }
        let f = line.split('\t').collect::<Vec<_>>();
        match f.as_slice() {
            ["UPTIME", v] if up.is_none() => {
                let n = v.parse::<f64>().map_err(|_| "thermal uptime invalid")?;
                require(n.is_finite() && n >= 0., "thermal uptime unknown")?;
                up = Some(n);
            }
            ["TEMP", p, v] if sensor_path(p) => {
                let n = v.parse::<f64>().map_err(|_| "thermal sample raw invalid")?;
                require(n.is_finite(), "thermal sample raw nonfinite")?;
                require(
                    values.insert((*p).to_owned(), n).is_none(),
                    "duplicate thermal sample reading",
                )?;
            }
            _ => return Err("thermal sample record malformed".into()),
        }
    }
    require(
        up.is_some()
            && values.keys().collect::<BTreeSet<_>>() == paths.iter().collect::<BTreeSet<_>>(),
        "thermal sample missing/extra sensor identity",
    )?;
    Ok(json!({"uptime_s":up,"readings":values,"thermal_status":hal}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn typed_inventory_rejects_unknown_mixed_identity_and_missing_values() {
        let s="#phonefarm_thermal_metadata v1\nBOOT\tb\nSENSOR\t/sys/class/thermal/thermal_zone17/temp\tcpu-2-1-1\tcpu\tmilli_celsius\nTRIP\t/sys/class/thermal/thermal_zone17/temp\tpassive\t108000\n#end\n";
        assert_eq!(
            parse_metadata(s, "b").unwrap()["sensors"][0]["trip_points"][0]["raw"],
            108000.0
        );
        assert!(parse_metadata(s, "other").is_err());
        assert!(parse_metadata(&s.replace("milli_celsius", "arbitrary"), "b").is_err());
        assert!(parse_metadata(
            &s.replace(
                "/sys/class/thermal/thermal_zone17/temp",
                "/sys/kernel/debug/thermal/temp"
            ),
            "b"
        )
        .is_err());
        assert!(parse_metadata(&s.replace("#end\n", ""), "b").is_err());
    }
    #[test]
    fn sample_requires_exact_frozen_paths_and_finite_monotonic_clock_value() {
        let ps = vec!["/sys/class/power_supply/battery/temp".into()];
        let s="#phonefarm_thermal_sample v1\nUPTIME\t12.5\nTEMP\t/sys/class/power_supply/battery/temp\t393\n#end\n";
        assert_eq!(parse_sample(s, &ps).unwrap()["readings"][&ps[0]], 393.0);
        for bad in [
            s.replace("12.5", "NaN"),
            s.replace("393", "NaN"),
            s.replace("#end\n", ""),
            s.replace("battery/temp", "usb/temp"),
        ] {
            assert!(parse_sample(&bad, &ps).is_err());
        }
        assert!(snapshot_script(&["/sys/class/power_supply/battery/temp;write".into()]).is_err());
    }
}

pub(super) fn hal_status(raw: &str) -> Result<u8, String> {
    let statuses = raw
        .lines()
        .filter_map(|l| l.strip_prefix("Thermal Status: "))
        .collect::<Vec<_>>();
    let overrides = raw
        .lines()
        .filter_map(|l| l.strip_prefix("IsStatusOverride: "))
        .collect::<Vec<_>>();
    let ready = raw
        .lines()
        .filter_map(|l| l.strip_prefix("HAL Ready: "))
        .collect::<Vec<_>>();
    require(
        statuses.len() == 1 && overrides == ["false"] && ready == ["true"],
        "thermal HAL unavailable/override/ambiguous",
    )?;
    let n = statuses[0]
        .parse::<u8>()
        .map_err(|_| "thermal HAL status invalid")?;
    require(n <= 6, "thermal HAL status unknown")?;
    Ok(n)
}
pub(super) fn hal_domain_limits(raw: &str) -> Result<BTreeMap<String, f64>, String> {
    hal_status(raw)?;
    let re=regex::Regex::new(r"^\s*TemperatureThreshold\{mType=([0-9]+), mName=([^,}\n]{1,128}), mHotThrottlingThresholds=\[([^]]*)\]").map_err(|e|e.to_string())?;
    let mut out = BTreeMap::<String, f64>::new();
    let mut identities = BTreeSet::new();
    for l in raw.lines() {
        let Some(m) = re.captures(l) else {
            require(
                !l.contains("TemperatureThreshold"),
                "malformed HAL threshold record",
            )?;
            continue;
        };
        let ty = m[1]
            .parse::<u32>()
            .map_err(|_| "invalid thermal HAL type")?;
        require(ty <= 255, "unknown thermal HAL type encoding")?;
        if ty > 3 {
            continue;
        } // Parsed non-selected domain, never evidence for CPU/GPU/skin/battery.
        require(
            identities.insert((m[1].to_owned(), m[2].to_owned())),
            "duplicate thermal HAL threshold identity",
        )?;
        let vals = m[3].split(',').map(str::trim).collect::<Vec<_>>();
        require(vals.len() == 7, "thermal HAL threshold enum width unknown")?;
        let severe = vals[3]
            .parse::<f64>()
            .map_err(|_| "thermal HAL severe threshold unknown")?;
        require(
            severe.is_finite() && severe > 0. && severe <= 150.,
            "thermal HAL severe threshold invalid",
        )?;
        let d = match &m[1] {
            "0" => "cpu",
            "1" => "gpu",
            "2" => "battery",
            "3" => "skin",
            _ => unreachable!(),
        };
        out.entry(d.into())
            .and_modify(|v| *v = v.min(severe))
            .or_insert(severe);
    }
    require(
        ["cpu", "gpu", "skin", "battery"]
            .iter()
            .all(|d| out.contains_key(*d)),
        "thermal HAL required domain threshold absent",
    )?;
    Ok(out)
}
pub(super) fn bind_hal_limits(inventory: &mut Value, hal: &str) -> Result<(), String> {
    let limits = hal_domain_limits(hal)?;
    for s in inventory["sensors"]
        .as_array_mut()
        .ok_or("thermal inventory sensors missing")?
    {
        let d = s["domain"].as_str().ok_or("thermal domain")?;
        let cap = *limits.get(d).ok_or("thermal HAL domain unavailable")?;
        let raw = match s["unit"].as_str() {
            Some("milli_celsius") => cap * 1000.,
            Some("deci_celsius") => cap * 10.,
            _ => return Err("thermal trip unit unsupported".into()),
        };
        s["trip_points"]
            .as_array_mut()
            .ok_or("thermal trips")?
            .push(json!({"kind":"hal_severe","raw":raw}));
    }
    Ok(())
}
#[cfg(test)]
mod hal_tests {
    use super::*;
    #[test]
    fn status_must_be_unique_connected_and_not_overridden() {
        let s = "IsStatusOverride: false\nThermal Status: 2\nHAL Ready: true\n";
        assert_eq!(hal_status(s).unwrap(), 2);
        for bad in [
            s.replace("false", "true"),
            s.replace("2", "unknown"),
            s.replace("true", "false"),
            format!("{s}Thermal Status: 1\n"),
        ] {
            assert!(hal_status(&bad).is_err());
        }
    }
    #[test]
    fn hal_caps_use_known_severe_enum_index_not_cached_current_temperature() {
        let mut s = "IsStatusOverride: false\nThermal Status: 2\nHAL Ready: true\n".to_owned();
        for (ty, name, t) in [
            (0, "CPU0", 95),
            (1, "GPU0", 95),
            (2, "battery", 80),
            (3, "skin", 60),
        ] {
            s+=&format!("TemperatureThreshold{{mType={ty}, mName={name}, mHotThrottlingThresholds=[NaN, NaN, NaN, {t}.0, NaN, NaN, 115.0], mColdThrottlingThresholds=[]}}\n");
        }
        let m = hal_domain_limits(&s).unwrap();
        assert_eq!(m["cpu"], 95.);
        assert_eq!(m["skin"], 60.);
        assert!(hal_domain_limits(&s.replace("60.0", "NaN")).is_err());
        assert!(hal_domain_limits(&s.replace("mType=3", "mType=8")).is_err());
        let stricter=format!("{s}TemperatureThreshold{{mType=0, mName=CPU.high, mHotThrottlingThresholds=[NaN, NaN, NaN, 40.0, NaN, NaN, 115.0]}}\n");
        assert_eq!(hal_domain_limits(&stricter).unwrap()["cpu"], 40.0);
        assert!(
            hal_domain_limits(&stricter.replace("mName=CPU.high,", "invalidName=CPU.high,"))
                .is_err()
        );
    }
}

pub(crate) struct FrozenTemperature {
    pub profile: crate::thermal_profile::Profile,
    pub reference: crate::thermal_profile::ReferenceBand,
}
fn typed_metadata(v: &Value) -> Result<Vec<crate::thermal_profile::SensorMetadata>, String> {
    serde_json::from_value(v["sensors"].clone()).map_err(|e| e.to_string())
}
pub(crate) fn samples(
    raw: &str,
    paths: &[String],
) -> Result<Vec<crate::thermal_profile::Sample>, String> {
    let marker = "#phonefarm_thermal_sample v1\n";
    let mut out = Vec::new();
    require(raw.starts_with(marker), "thermal series prefix missing")?;
    for tail in raw.split(marker).skip(1) {
        let s = format!("{marker}{tail}");
        out.push(serde_json::from_value(parse_sample(&s, paths)?).map_err(|e| e.to_string())?);
    }
    require(
        !out.is_empty() && out.len() <= 512,
        "thermal series size invalid",
    )?;
    Ok(out)
}
/// Offline reconstruction from retained receiver raw bytes; no serialized cap or band is trusted.
pub(crate) fn reconstruct_raw(
    metadata: &str,
    hal: &str,
    warmup: &str,
    boot: &str,
) -> Result<FrozenTemperature, String> {
    let mut inventory = parse_metadata(metadata, boot)?;
    bind_hal_limits(&mut inventory, hal)?;
    let profile =
        crate::thermal_profile::resolve("loaded_steady_v1", &typed_metadata(&inventory)?)?;
    let paths = profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let readings = samples(warmup, &paths)?;
    let reference = crate::thermal_profile::freeze_reference_band(&profile, &readings)?;
    Ok(FrozenTemperature { profile, reference })
}
pub(crate) fn parse_window_raw(
    raw: &str,
    paths: &[String],
) -> Result<(Vec<crate::thermal_profile::Sample>, Value), String> {
    let (body, receipt) = raw
        .split_once("#phonefarm_thermal_window_complete ")
        .ok_or("thermal sampler completion missing")?;
    require(
        receipt.lines().count() == 1 && raw.ends_with('\n'),
        "thermal sampler terminal receipt malformed",
    )?;
    let mut fields = BTreeMap::new();
    for f in receipt.trim().split_whitespace() {
        let (k, v) = f
            .split_once('=')
            .ok_or("thermal sampler receipt field malformed")?;
        require(
            fields.insert(k, v).is_none(),
            "duplicate thermal sampler receipt field",
        )?;
    }
    require(
        fields.keys().copied().collect::<BTreeSet<_>>()
            == BTreeSet::from(["dur", "interval", "start", "end", "samples"]),
        "thermal sampler completion keys differ",
    )?;
    let value = |k| {
        fields[k]
            .parse::<f64>()
            .map_err(|_| "thermal sampler completion numeric value invalid".to_owned())
    };
    let dur = value("dur")?;
    let ivl = value("interval")?;
    let start = value("start")?;
    let end = value("end")?;
    let count = value("samples")?;
    require(
        [dur, ivl, start, end, count].iter().all(|n| n.is_finite())
            && (2. ..=1000.).contains(&dur)
            && ivl == 1.
            && start >= 0.
            && end - start >= dur
            && count.fract() == 0.,
        "thermal sampler duration/clock unknown",
    )?;
    let readings = samples(body, paths)?;
    require(
        count == readings.len() as f64
            && readings.first().is_some_and(|s| s.uptime_s >= start)
            && readings.last().is_some_and(|s| s.uptime_s <= end)
            && readings.last().unwrap().uptime_s - start >= dur,
        "thermal sampler samples do not match complete window",
    )?;
    let actual_count = readings.len();
    Ok((
        readings,
        json!({"duration_s":dur,"interval_s":ivl,"start_uptime_s":start,"end_uptime_s":end,"samples":actual_count}),
    ))
}
fn warmup_script(profile: &crate::thermal_profile::Profile) -> Result<String, String> {
    let point = sampling_script(profile, None)?;
    let mut script = format!("#!/system/bin/sh\nset -eu\n{}", clock_helpers()?);
    for i in 0..7 {
        script += &format!("if point=$(sh -c {}); then :; else code=$?; printf '%s\\n' \"$point\"; exit \"$code\"; fi\n printf '%s\\n' \"$point\"\nprevious=$(thermal_point_uptime \"$point\") || exit 81\n", quote(&point));
        if i < 6 {
            script += "thermal_wait_until \"$(thermal_deadline \"$previous\" 5)\" \"$(thermal_deadline \"$previous\" 6)\" \"$previous\" || exit 81\n";
        }
    }
    Ok(script)
}
fn clock_helpers() -> Result<&'static str, String> {
    WORKER
        .split_once("#phonefarm_thermal_clock_begin\n")
        .and_then(|(_, rest)| rest.split_once("#phonefarm_thermal_clock_end\n"))
        .map(|(body, _)| body)
        .ok_or_else(|| "trusted worker thermal clock helper missing".into())
}
#[cfg(test)]
mod cadence_tests {
    use super::*;
    use std::process::Command;
    fn host_clock_script(body: &str) -> std::process::Output {
        // Only the /proc/uptime reader is adapted to the host monotonic clock.
        // The frozen worker's actual scheduling functions execute unchanged.
        let script = format!(
            "set -eu\n{}\ncut() {{ python3 -c 'import time; print(time.monotonic())'; }}\n{body}",
            clock_helpers().unwrap()
        );
        Command::new("sh").arg("-c").arg(script).output().unwrap()
    }
    #[test]
    fn real_slow_body_uses_remaining_wait_instead_of_post_query_sleep() {
        let out = host_clock_script(
            r#"start=$(thermal_uptime)
sleep .45
thermal_wait_until "$(thermal_deadline "$start" 0.5)" "$(thermal_deadline "$start" 0.9)" "$start"
end=$(thermal_uptime)
awk -v a="$start" -v b="$end" 'BEGIN {exit !(b-a>=.5 && b-a<=.9)}'
start=$(thermal_uptime)
sleep .45; sleep .5
end=$(thermal_uptime)
awk -v a="$start" -v b="$end" 'BEGIN {exit !(b-a>.9)}'
printf 'deadline_body_passed_legacy_delay_exceeded_budget\n'
"#,
        );
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert!(String::from_utf8_lossy(&out.stdout).contains("deadline_body_passed"));
    }
    #[test]
    fn actual_overrun_and_backward_unknown_clocks_refuse_without_catchup() {
        let overrun = host_clock_script(
            r#"start=$(thermal_uptime)
sleep 1.05
thermal_wait_until "$(thermal_deadline "$start" 0.5)" "$(thermal_deadline "$start" 0.9)" "$start"
printf 'must_not_collect_next_point\n'
"#,
        );
        assert!(!overrun.status.success());
        assert!(!String::from_utf8_lossy(&overrun.stdout).contains("must_not_collect"));
        for value in ["99", "NaN"] {
            let out = host_clock_script(&format!(
                "cut() {{ printf '%s\\n' '{value}'; }}\nthermal_wait_until 105 106 100\nprintf 'must_not_collect_next_point\\n'\n"
            ));
            assert!(!out.status.success());
            assert!(!String::from_utf8_lossy(&out.stdout).contains("must_not_collect"));
        }
    }
}
#[cfg(test)]
mod capability_entry_tests {
    use super::*;
    #[test]
    fn actual_worker_purpose_guard_rejects_performance_before_ownership() {
        use std::process::Command;
        let dir = std::env::temp_dir().join(format!("pf-cap-purpose-{}", std::process::id()));
        fs::create_dir(&dir).unwrap();
        fs::write(dir.join("thermal-purpose.txt"), b"capability_absolute_v1\n").unwrap();
        let guard = WORKER
            .split_once("# A capability diagnostic session")
            .unwrap()
            .1
            .split_once(r#"R="$D/$RUN""#)
            .unwrap()
            .0;
        for mode in [
            "sample",
            "probe",
            "reconcile",
            "control_probe",
            "reconcile_controls",
        ] {
            let script = format!(
                "set -eu\nD={}\nMODE={}\n# A capability diagnostic session{}\nprintf entered\n",
                quote(dir.to_str().unwrap()),
                quote(mode),
                guard
            );
            let out = Command::new("sh").arg("-c").arg(script).output().unwrap();
            let allowed = matches!(mode, "control_probe" | "reconcile_controls");
            assert_eq!(out.status.success(), allowed, "{mode}: {:?}", out);
            if !allowed {
                assert_eq!(out.status.code(), Some(87));
                assert!(out.stdout.is_empty());
            }
        }
        fs::remove_file(dir.join("thermal-purpose.txt")).unwrap();
        fs::remove_dir(dir).unwrap();
    }
}

fn window_script() -> Result<String, String> {
    Ok(format!("#!/system/bin/sh\nset -eu\n{}{}", clock_helpers()?,
    "dur=${1:?duration}; interval=${2:?interval}\ncase $dur in ''|*[!0-9]*) exit 81;; esac\n[ \"$dur\" -ge 2 ] && [ \"$dur\" -le 1000 ] && [ \"$interval\" = 1 ] || exit 81\nD=$(dirname \"$0\"); start=$(thermal_uptime) || exit 81; n=0\nwhile :; do\n  if point=$(sh \"$D/thermal_sample.sh\"); then :; else code=$?; printf '%s\\n' \"$point\"; exit \"$code\"; fi\n  printf '%s\\n' \"$point\"; n=$((n+1))\n  if [ \"$n\" = 1 ]; then printf '%s\\n' \"$start\" > \"${3:?ready receipt}\"; fi\n  end=$(thermal_uptime) || exit 81\n  up=$(thermal_point_uptime \"$point\") || exit 81\n  if awk -v a=\"$start\" -v b=\"$up\" -v d=\"$dur\" 'BEGIN{exit !(b-a>=d)}'; then break; fi\n  thermal_wait_until \"$(thermal_deadline \"$up\" \"$interval\")\" \"$(thermal_deadline \"$up\" 6)\" \"$up\" || exit 81\ndone\nprintf '#phonefarm_thermal_window_complete dur=%s interval=%s start=%s end=%s samples=%s\\n' \"$dur\" \"$interval\" \"$start\" \"$end\" \"$n\"\n"))
}
pub(super) fn frozen_names() -> &'static [&'static str] {
    &[
        "thermal_metadata.sh",
        "thermal_metadata.raw.tsv",
        "thermal_inventory.json",
        "thermal_hal.raw.txt",
        "thermal_profile.json",
        "thermal-rules.json",
        "thermal_warmup.sh",
        "thermal_warmup.raw.tsv",
        "thermal_reference.json",
        "thermal_sample.sh",
        "thermal_sampler.sh",
        "thermal-evidence.json",
    ]
}
fn sampling_script(
    profile: &crate::thermal_profile::Profile,
    reference: Option<&crate::thermal_profile::ReferenceBand>,
) -> Result<String, String> {
    let paths = profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let mut s = snapshot_script(&paths)?;
    let types = profile
        .sensors
        .iter()
        .filter(|s| s.domain != crate::thermal_profile::Domain::Battery)
        .collect::<Vec<_>>();
    if !types.is_empty() {
        let type_paths = types
            .iter()
            .map(|s| quote(&s.path.replace("/temp", "/type")))
            .collect::<Vec<_>>()
            .join(" ");
        let mut check = format!(
            "types=$(cat {type_paths})\nset -- $types\n[ \"$#\" -eq {} ] || exit 84\n",
            types.len()
        );
        for sensor in &types {
            check += &format!(
                "[ \"$1\" = {} ] || exit 84; shift\n",
                quote(&sensor.sensor_type)
            );
        }
        s = s.replace("values=$(cat ", &format!("{check}values=$(cat "));
    }
    let mut setup = String::new();
    for (i, t) in profile.sensors.iter().enumerate() {
        let (scale, unit) = match t.unit {
            crate::thermal_profile::Unit::MilliCelsius => (1000., "milli_celsius"),
            crate::thermal_profile::Unit::DeciCelsius => (10., "deci_celsius"),
            crate::thermal_profile::Unit::Celsius => (1., "celsius"),
            _ => return Err("selected thermal unit unknown".into()),
        };
        let (lo, hi) = if let Some(r) = reference {
            let b = r
                .bands
                .get(&t.path)
                .ok_or("thermal reference sensor missing")?;
            (b.low_c, b.high_c)
        } else {
            (-50., t.operating_cap_c)
        };
        setup += &format!(
            "scale[{}]={scale:.10}; low[{}]={lo:.10}; high[{}]={hi:.10}; expected_path[{}]=\"{}\"; declared_unit[{}]=\"{unit}\"; operating_cap[{}]={:.10};",
            i + 1,
            i + 1,
            i + 1,
            i + 1,
            t.path,
            i + 1,
            i + 1,
            t.operating_cap_c
        );
    }
    let gate = REJECTION_GATE
        .replace("__FROZEN_SENSOR_SETUP__", &setup)
        .replace("__EXPECTED_COUNT__", &profile.sensors.len().to_string())
        .replace(
            "__BOUNDS_SOURCE__",
            if reference.is_some() {
                "reference_band"
            } else {
                "absolute_cap"
            },
        );
    // AWK normally emitted no stdout. Its bounded failure receipt goes only to
    // stderr, using the same values snapshot before the preserved exit82.
    let check = format!(
        "printf '%s\\n' \"$values\" | awk {} >&2 || exit 82\n",
        quote(&gate)
    );
    s = s.replace("set -- $values\n", &(check + "set -- $values\n"));
    // The same captured HAL bytes are printed and checked; no second query gives a different status.
    s=s.replace("printf '#hal_begin\\n'\ntimeout 8 dumpsys thermalservice\nprintf '#hal_end\\n'", "hal=$(timeout 8 dumpsys thermalservice)\nprintf '#hal_begin\\n%s\\n#hal_end\\n' \"$hal\"\nprintf '%s\\n' \"$hal\" | awk '/^Thermal Status: /{n++; if ($3 !~ /^[0-2]$/) bad=1} /^IsStatusOverride: /{o++; if ($2!=\"false\") bad=1} /^HAL Ready: /{r++; if ($3!=\"true\") bad=1} END{exit (bad || n!=1 || o!=1 || r!=1)}' || exit 83");
    Ok(s)
}
pub(super) fn freeze_loaded(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    serial: &str,
    boot: &str,
) -> Result<FrozenTemperature, String> {
    freeze_preset(t, lease, dir, serial, boot, "loaded_steady_v1")
}
pub(super) fn freeze_capability(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    serial: &str,
    boot: &str,
) -> Result<FrozenTemperature, String> {
    require(
        read_json(&dir.join("request.json"))?["schema"] == "phonefarm/control_probe_request@1",
        "capability preset is control-only",
    )?;
    freeze_preset(t, lease, dir, serial, boot, "capability_absolute_v1")
}
fn reference_for(
    profile: &crate::thermal_profile::Profile,
    readings: &[crate::thermal_profile::Sample],
) -> Result<crate::thermal_profile::ReferenceBand, String> {
    if profile.id == "capability_absolute_v1" {
        crate::thermal_profile::freeze_capability_diagnostics(profile, readings)
    } else {
        crate::thermal_profile::freeze_reference_band(profile, readings)
    }
}
fn freeze_preset(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    serial: &str,
    boot: &str,
    preset: &str,
) -> Result<FrozenTemperature, String> {
    let raw = collect_metadata(t, lease, dir, boot)?;
    let hal = checked(
        t,
        "thermal_hal_metadata",
        "timeout 8 dumpsys thermalservice",
        None,
        10,
        256 * 1024,
        lease,
    )?;
    let halref = save(dir, "thermal_hal.raw.txt", &hal)?;
    let mut inv = raw["inventory"].clone();
    bind_hal_limits(
        &mut inv,
        std::str::from_utf8(&hal).map_err(|e| e.to_string())?,
    )?;
    let profile = crate::thermal_profile::resolve(preset, &typed_metadata(&inv)?)?;
    let profile_sha = crate::thermal_profile::profile_sha256(&profile)?;
    save_json(
        dir,
        "thermal_profile.json",
        &serde_json::to_value(&profile).map_err(|e| e.to_string())?,
    )?;
    let warmup_script = warmup_script(&profile)?;
    save(dir, "thermal_warmup.sh", warmup_script.as_bytes())?;
    t.input_hashes
        .insert(dir.join("thermal_warmup.sh"), sha(warmup_script.as_bytes()));
    save_json(
        dir,
        "thermal-rules.json",
        &json!({"schema":"phonefarm/frozen_thermal_producer@1","temperature_profile_id":preset,
        "profile_sha256":profile_sha,"metadata_raw":raw["raw"],"hal_raw":halref,"warmup_script_sha256":sha(warmup_script.as_bytes()),
        "actual_serial":serial,"boot_id":boot,"freeze_at":if t.origin=="synthetic"{(chrono::Utc::now()-chrono::Duration::hours(5)).to_rfc3339()}else{now()},"scope":"temperature_only_before_control"}),
    )?;
    journal(
        dir,
        json!({"event":"thermal_receiver_rules_frozen_before_warmup","profile_sha256":profile_sha,"boot_id":boot,"at":now()}),
    )?;
    let warmup_started_at = if t.origin == "synthetic" {
        (chrono::Utc::now() - chrono::Duration::hours(5) + chrono::Duration::seconds(1))
            .to_rfc3339()
    } else {
        now()
    };
    let bytes = checked(
        t,
        "thermal_baseline_only_warmup",
        "sh",
        Some(&dir.join("thermal_warmup.sh")),
        90,
        2 * 1024 * 1024,
        lease,
    )?;
    let warmup_finished_at = if t.origin == "synthetic" {
        (chrono::Utc::now() - chrono::Duration::hours(5) + chrono::Duration::seconds(31))
            .to_rfc3339()
    } else {
        now()
    };
    save(dir, "thermal_warmup.raw.tsv", &bytes)?;
    let paths = profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let readings = samples(
        std::str::from_utf8(&bytes).map_err(|e| e.to_string())?,
        &paths,
    )?;
    let reference = reference_for(&profile, &readings)?;
    save_json(
        dir,
        "thermal_reference.json",
        &serde_json::to_value(&reference).map_err(|e| e.to_string())?,
    )?;
    let script = sampling_script(
        &profile,
        if preset == "capability_absolute_v1" {
            None
        } else {
            Some(&reference)
        },
    )?;
    if preset == "capability_absolute_v1" {
        save(dir, "thermal-purpose.txt", b"capability_absolute_v1\n")?;
    }
    save(dir, "thermal_sample.sh", script.as_bytes())?;
    t.input_hashes
        .insert(dir.join("thermal_sample.sh"), sha(script.as_bytes()));
    save(dir, "thermal_sampler.sh", window_script()?.as_bytes())?;
    let rules = read_json(&dir.join("thermal-rules.json"))?;
    save_json(
        dir,
        "thermal-evidence.json",
        &json!({"schema":"phonefarm/frozen_thermal_evidence@1",
        "actual_serial":serial,"boot_id":boot,"rules_frozen_at":rules["freeze_at"],"warmup_started_at":warmup_started_at,"warmup_finished_at":warmup_finished_at,
        "metadata_raw":file_ref(dir,"thermal_metadata.raw.tsv")?,"hal_raw":file_ref(dir,"thermal_hal.raw.txt")?,"warmup_raw":file_ref(dir,"thermal_warmup.raw.tsv")?,
        "profile":file_ref(dir,"thermal_profile.json")?,"reference":file_ref(dir,"thermal_reference.json")?,"profile_sha256":profile_sha,
        "reference_sha256":file_ref(dir,"thermal_reference.json")?["sha256"],"clock_scope":if t.origin=="synthetic"{"synthetic_virtual_timing"}else{"host_utc_and_device_proc_uptime_separate"}}),
    )?;
    Ok(FrozenTemperature { profile, reference })
}
pub(super) fn load_frozen(dir: &Path) -> Result<FrozenTemperature, String> {
    load_preset(dir, "loaded_steady_v1")
}
pub(super) fn load_capability_frozen(dir: &Path) -> Result<FrozenTemperature, String> {
    let request = read_json(&dir.join("request.json"))?;
    require(
        request["schema"] == "phonefarm/control_probe_request@1"
            && request["temperature_profile_id"] == "capability_absolute_v1",
        "capability preset is control-only",
    )?;
    require(
        bounded(&dir.join("thermal-purpose.txt"), 128)? == b"capability_absolute_v1\n",
        "capability purpose marker missing or changed",
    )?;
    load_preset(dir, "capability_absolute_v1")
}
fn load_preset(dir: &Path, preset: &str) -> Result<FrozenTemperature, String> {
    let state = read_json(&dir.join("state.json"))?;
    let rules = read_json(&dir.join("thermal-rules.json"))?;
    let raw = bounded(&dir.join("thermal_metadata.raw.tsv"), 256 * 1024)?;
    let mut inv = parse_metadata(
        std::str::from_utf8(&raw).map_err(|e| e.to_string())?,
        state["boot_id"].as_str().ok_or("thermal frozen boot")?,
    )?;
    let hal = bounded(&dir.join("thermal_hal.raw.txt"), 256 * 1024)?;
    bind_hal_limits(
        &mut inv,
        std::str::from_utf8(&hal).map_err(|e| e.to_string())?,
    )?;
    let profile = crate::thermal_profile::resolve(preset, &typed_metadata(&inv)?)?;
    require(
        rules["schema"] == "phonefarm/frozen_thermal_producer@1"
            && rules["temperature_profile_id"] == preset
            && rules["profile_sha256"] == crate::thermal_profile::profile_sha256(&profile)?
            && rules["actual_serial"] == state["serial"]
            && rules["boot_id"] == state["boot_id"],
        "frozen thermal identity/profile differs",
    )?;
    require(
        rules["metadata_raw"]["sha256"] == sha(&raw) && rules["hal_raw"]["sha256"] == sha(&hal),
        "thermal raw metadata binding differs",
    )?;
    let retained: crate::thermal_profile::Profile =
        serde_json::from_value(read_json(&dir.join("thermal_profile.json"))?)
            .map_err(|e| e.to_string())?;
    require(profile == retained, "frozen thermal preset was modified")?;
    let paths = profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let warm = bounded(&dir.join("thermal_warmup.raw.tsv"), 2 * 1024 * 1024)?;
    let readings = samples(
        std::str::from_utf8(&warm).map_err(|e| e.to_string())?,
        &paths,
    )?;
    let reference = reference_for(&profile, &readings)?;
    let retained: crate::thermal_profile::ReferenceBand =
        serde_json::from_value(read_json(&dir.join("thermal_reference.json"))?)
            .map_err(|e| e.to_string())?;
    require(
        reference == retained,
        "thermal reference differs from original raw warmup",
    )?;
    require(
        bounded(&dir.join("thermal_sample.sh"), 2 * 1024 * 1024)?
            == sampling_script(
                &profile,
                if preset == "capability_absolute_v1" {
                    None
                } else {
                    Some(&reference)
                },
            )?
            .as_bytes(),
        "thermal readback execution script differs",
    )?;
    require(
        bounded(&dir.join("thermal_metadata.sh"), 2 * 1024 * 1024)? == METADATA.as_bytes(),
        "thermal metadata tool differs",
    )?;
    require(
        bounded(&dir.join("thermal_warmup.sh"), 2 * 1024 * 1024)?
            == warmup_script(&profile)?.as_bytes()
            && rules["warmup_script_sha256"] == sha(warmup_script(&profile)?.as_bytes())
            && bounded(&dir.join("thermal_sampler.sh"), 2 * 1024 * 1024)?
                == window_script()?.as_bytes(),
        "thermal frozen executable differs",
    )?;
    let evidence = read_json(&dir.join("thermal-evidence.json"))?;
    require(
        evidence["schema"] == "phonefarm/frozen_thermal_evidence@1"
            && evidence["actual_serial"] == state["serial"]
            && evidence["boot_id"] == state["boot_id"]
            && evidence["rules_frozen_at"] == rules["freeze_at"]
            && evidence["profile_sha256"] == crate::thermal_profile::profile_sha256(&profile)?,
        "thermal timing/identity binding differs",
    )?;
    for (key, file) in [
        ("metadata_raw", "thermal_metadata.raw.tsv"),
        ("hal_raw", "thermal_hal.raw.txt"),
        ("warmup_raw", "thermal_warmup.raw.tsv"),
        ("profile", "thermal_profile.json"),
        ("reference", "thermal_reference.json"),
    ] {
        require(
            evidence[key] == file_ref(dir, file)?,
            "thermal evidence frozen bytes differ",
        )?;
    }
    let when = |key: &str| -> Result<chrono::DateTime<chrono::FixedOffset>, String> {
        chrono::DateTime::parse_from_rfc3339(
            evidence[key].as_str().ok_or("thermal timing missing")?,
        )
        .map_err(|e| e.to_string())
    };
    require(
        when("rules_frozen_at")? < when("warmup_started_at")?
            && when("warmup_started_at")? < when("warmup_finished_at")?,
        "thermal warmup chronology unknown",
    )?;
    require(
        evidence["reference_sha256"] == file_ref(dir, "thermal_reference.json")?["sha256"],
        "thermal reference fingerprint differs",
    )?;
    Ok(FrozenTemperature { profile, reference })
}
pub(super) fn loaded_snapshot(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    temperature: &FrozenTemperature,
    label: &str,
) -> Result<Value, String> {
    require(
        temperature.profile.id == "loaded_steady_v1",
        "performance point requires loaded reference preset",
    )?;
    thermal_snapshot(t, lease, dir, temperature, label)
}
pub(super) fn capability_snapshot(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    temperature: &FrozenTemperature,
    label: &str,
) -> Result<Value, String> {
    require(
        temperature.profile.id == "capability_absolute_v1",
        "capability point requires absolute preset",
    )?;
    thermal_snapshot(t, lease, dir, temperature, label)
}
fn thermal_snapshot(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    temperature: &FrozenTemperature,
    label: &str,
) -> Result<Value, String> {
    let raw = checked(
        t,
        "thermal_snapshot",
        "sh",
        Some(&dir.join("thermal_sample.sh")),
        10,
        256 * 1024,
        lease,
    )?;
    save(dir, &format!("{label}-thermal.raw.tsv"), &raw)?;
    let paths = temperature
        .profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let readings = samples(
        std::str::from_utf8(&raw).map_err(|e| e.to_string())?,
        &paths,
    )?;
    require(readings.len() == 1, "thermal point expected one snapshot")?;
    let assessed =
        crate::thermal_profile::evaluate_admission_snapshot(&temperature.profile, &readings[0])?;
    require(
        assessed.compliant,
        "loaded thermal point is outside frozen operational profile",
    )?;
    if temperature.profile.id != "capability_absolute_v1" {
        for (path, v) in &assessed.normalized_c {
            let band = temperature
                .reference
                .bands
                .get(path)
                .ok_or("thermal point reference missing")?;
            require(
                *v >= band.low_c && *v <= band.high_c,
                "loaded point outside frozen baseline-only reference band",
            )?;
        }
    }
    let receipt = json!({"checked_at":now(),"profile_sha256":crate::thermal_profile::profile_sha256(&temperature.profile)?,"raw":readings[0],"assessment":assessed,"scope":"temperature_single_point_only"});
    save_json(dir, &format!("{label}-thermal.json"), &receipt)?;
    Ok(receipt)
}
pub(super) fn assess_hold(
    dir: &Path,
    raw: &str,
    temperature: &FrozenTemperature,
) -> Result<Value, String> {
    let paths = temperature
        .profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let capability = temperature.profile.id == "capability_absolute_v1";
    let (body, completion) = if capability {
        let (body, tail) = raw
            .split_once("#phonefarm_control_hold_complete ")
            .ok_or("control thermal hold completion missing")?;
        require(
            tail.lines().count() == 1 && raw.ends_with('\n'),
            "control hold completion malformed",
        )?;
        let mut fields = BTreeMap::new();
        for field in tail.trim().split_whitespace() {
            let (k, v) = field
                .split_once('=')
                .ok_or("hold completion field malformed")?;
            require(
                fields.insert(k, v).is_none(),
                "duplicate hold completion field",
            )?;
        }
        require(
            fields.keys().copied().collect::<BTreeSet<_>>()
                == BTreeSet::from(["start", "end", "samples", "profile"])
                && fields["profile"] == "capability_absolute_v1"
                && fields["samples"] == "4",
            "hold completion keys/count/purpose differ",
        )?;
        let number = |key| {
            fields[key]
                .parse::<f64>()
                .map_err(|_| "hold completion clock invalid".to_string())
        };
        let start = number("start")?;
        let end = number("end")?;
        require(
            start.is_finite() && end.is_finite() && start >= 0. && end - start >= 11.,
            "control hold shorter than 11s or clock unknown",
        )?;
        (
            body,
            json!({"start_uptime_s":start,"end_uptime_s":end,"samples":4,"profile_id":"capability_absolute_v1"}),
        )
    } else {
        (raw, Value::Null)
    };
    let readings = samples(body, &paths)?;
    if capability {
        let start = completion["start_uptime_s"].as_f64().ok_or("hold start")?;
        let end = completion["end_uptime_s"].as_f64().ok_or("hold end")?;
        require(
            readings.len() == 4
                && readings[0].uptime_s <= start
                && start - readings[0].uptime_s <= 6.
                && readings[1].uptime_s >= start + 1.
                && readings[2].uptime_s >= start + 6.
                && readings[3].uptime_s >= start + 11.
                && readings[3].uptime_s <= end
                && end - readings[3].uptime_s <= 6.,
            "control thermal readings do not cover the complete registered hold",
        )?;
    }
    let result = if capability {
        crate::thermal_profile::evaluate_window(&temperature.profile, &readings)?
    } else {
        crate::thermal_profile::evaluate_against_reference(
            &temperature.profile,
            &temperature.reference,
            &readings,
        )?
    };
    require(
        result.compliant,
        "control hold temperature escaped frozen reference/operational window",
    )?;
    let report = json!({"temperature_profile_id":temperature.profile.id,"stable_reference_required":!capability,"completion":completion,"diagnostic_reference":if capability{serde_json::to_value(&temperature.reference).map_err(|e|e.to_string())?}else{Value::Null},"profile_sha256":crate::thermal_profile::profile_sha256(&temperature.profile)?,"assessment":result,"scope":"provided_control_hold_temperature_readings_only"});
    save_json(dir, "control-hold-thermal.json", &report)?;
    Ok(report)
}
