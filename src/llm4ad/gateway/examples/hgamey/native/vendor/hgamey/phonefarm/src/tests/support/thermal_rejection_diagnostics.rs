//! Internal unit tests: actual generated shell, owned host stubs only.
use super::*;
use crate::thermal_profile::{
    Band, Domain, Profile, ProfileSensor, ReferenceBand, Unit, WindowRules,
};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

static NEXT: AtomicUsize = AtomicUsize::new(0);
static SHELL_FIXTURE: Mutex<()> = Mutex::new(());
const HAL: &str = "IsStatusOverride: false\nThermal Status: 0\nHAL Ready: true\n";

struct Fixture {
    dir: PathBuf,
    retained: bool,
    profile: Profile,
    reference: ReferenceBand,
    values: Vec<String>,
}
impl Fixture {
    fn new(count: usize) -> Self {
        let retained = std::env::var_os("PHONEFARM_THERMAL_DIAGNOSTICS_ARTIFACT_DIR");
        let parent = retained
            .clone()
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        fs::create_dir_all(&parent).unwrap();
        let dir = parent.join(format!(
            "thermal-shell-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        fs::create_dir(&dir).unwrap();
        let dir = dir.canonicalize().unwrap();
        fs::create_dir(dir.join("bin")).unwrap();
        let sensors = (0..count)
            .map(|i| {
                let battery = i + 1 == count;
                ProfileSensor {
                    path: if battery {
                        "/sys/class/power_supply/battery/temp".into()
                    } else {
                        format!("/sys/class/thermal/thermal_zone{i}/temp")
                    },
                    sensor_type: if battery {
                        "battery".into()
                    } else {
                        format!("cpu-{i}")
                    },
                    domain: if battery {
                        Domain::Battery
                    } else {
                        Domain::Cpu
                    },
                    unit: if battery {
                        Unit::DeciCelsius
                    } else {
                        Unit::MilliCelsius
                    },
                    admission_cap_c: if battery { 45. } else { 90. },
                    operating_cap_c: if battery { 45. } else { 90. },
                    cap_basis: "synthetic_host_fixture".into(),
                }
            })
            .collect::<Vec<_>>();
        let profile = Profile {
            schema: "phonefarm/temperature_profile@1".into(),
            id: "loaded_steady_v1".into(),
            metadata: vec![],
            sensors,
            excluded: vec![],
            window: WindowRules {
                warmup_min_seconds: 30.,
                warmup_min_samples: 6,
                sample_max_gap_seconds: 6.,
                warmup_max_range_c: 3.,
                reference_tolerance_c: 2.,
            },
        };
        let reference = ReferenceBand {
            schema: "synthetic_host_reference_only".into(),
            profile_sha256: "no_device_attestation".into(),
            bands: profile
                .sensors
                .iter()
                .map(|s| {
                    (
                        s.path.clone(),
                        if s.domain == Domain::Battery {
                            Band {
                                low_c: 37.3,
                                high_c: 41.3,
                            }
                        } else {
                            Band {
                                low_c: 78.,
                                high_c: 82.,
                            }
                        },
                    )
                })
                .collect(),
            warmup: vec![],
        };
        let values = (0..count)
            .map(|i| {
                if i + 1 == count {
                    "393".into()
                } else {
                    "80000".into()
                }
            })
            .collect();
        let this = Self {
            dir,
            retained: retained.is_some(),
            profile,
            reference,
            values,
        };
        this.tool("cut", "printf '12.50\\n'\n");
        this.tool("timeout", "shift; exec \"$@\"\n");
        this.tool("dumpsys", "[ \"$*\" = thermalservice ] || exit 99\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$THERMAL_FIXTURE/hal.txt\"\n");
        this.tool("cat", r#"case "$1" in
 /sys/class/thermal/thermal_zone*/type) kind=types ;;
 /sys/class/thermal/thermal_zone*/temp|/sys/class/power_supply/battery/temp) kind=values ;;
 *) exit 99 ;;
esac
printf '%s\n' "$kind" >> "$THERMAL_FIXTURE/calls.tsv"
while IFS= read -r line || [ -n "$line" ]; do printf '%s\n' "$line"; done < "$THERMAL_FIXTURE/$kind.tsv"
if [ "$kind" = values ] && [ "${THERMAL_MUTATE_AFTER_READ:-0}" = 1 ]; then
 i=1
 : > "$THERMAL_FIXTURE/values.tsv"
 while [ "$i" -le "$THERMAL_COUNT" ]; do
  if [ "$i" -eq "$THERMAL_COUNT" ]; then printf '393\n'; else printf '80000\n'; fi >> "$THERMAL_FIXTURE/values.tsv"
  i=$((i+1))
 done
fi
"#);
        this
    }
    fn tool(&self, name: &str, body: &str) {
        let p = self.dir.join("bin").join(name);
        fs::write(&p, format!("#!/bin/sh\nset -eu\n{body}")).unwrap();
        fs::set_permissions(p, fs::Permissions::from_mode(0o700)).unwrap();
    }
    fn run(&self, reference: bool, mutate: bool, label: &str, hal: &str) -> (i32, String, String) {
        // Cold script launch on macOS is not a device sampling deadline.
        let _guard = SHELL_FIXTURE.lock().unwrap_or_else(|e| e.into_inner());
        let script = sampling_script(
            &self.profile,
            if reference {
                Some(&self.reference)
            } else {
                None
            },
        )
        .unwrap();
        fs::write(self.dir.join("thermal_sample.sh"), &script).unwrap();
        fs::write(self.dir.join("values.tsv"), self.values.join("\n") + "\n").unwrap();
        fs::write(
            self.dir.join("types.tsv"),
            self.profile
                .sensors
                .iter()
                .filter(|s| s.domain != Domain::Battery)
                .map(|s| s.sensor_type.as_str())
                .collect::<Vec<_>>()
                .join("\n")
                + "\n",
        )
        .unwrap();
        fs::write(self.dir.join("hal.txt"), hal).unwrap();
        let stdout = fs::File::create(self.dir.join("stdout")).unwrap();
        let stderr = fs::File::create(self.dir.join("stderr")).unwrap();
        let mut child=Command::new("/bin/sh").args(["-c","/bin/sh \"$THERMAL_FIXTURE/thermal_sample.sh\" && printf 'owned_fake_candidate_only\\n' > \"$THERMAL_FIXTURE/candidate-marker\""])
            .env_clear()
            .env("THERMAL_FIXTURE",&self.dir).env("THERMAL_COUNT",self.profile.sensors.len().to_string())
            .env("THERMAL_MUTATE_AFTER_READ",if mutate{"1"}else{"0"}).env("LC_ALL","C")
            .env("PATH",format!("{}:/usr/bin:/bin",self.dir.join("bin").display()))
            .stdout(Stdio::from(stdout)).stderr(Stdio::from(stderr)).process_group(0).spawn().unwrap();
        let started = Instant::now();
        let status = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break status;
            }
            if started.elapsed() > Duration::from_secs(15) {
                unsafe {
                    libc::kill(-(child.id() as i32), libc::SIGKILL);
                }
                let _ = child.wait();
                panic!("owned host shell exceeded 15s fixture budget");
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        let out = fs::read_to_string(self.dir.join("stdout")).unwrap();
        let err = fs::read_to_string(self.dir.join("stderr")).unwrap();
        let code = status.code().unwrap();
        fs::write(self.dir.join("exit.json"),serde_json::to_vec_pretty(&json!({"source":"synthetic_host_shell_only","case":label,"exit_code":code,"candidate_marker":self.dir.join("candidate-marker").exists(),"sampling_script_sha256":sha(script.as_bytes()),"stdout_sha256":sha(out.as_bytes()),"stderr_sha256":sha(err.as_bytes()),"device_calls":0,"real_game_qualification":false})).unwrap()).unwrap();
        (code, out, err)
    }
    fn reject(&self, reference: bool, mutate: bool, label: &str) -> String {
        let (code, out, err) = self.run(reference, mutate, label, HAL);
        assert_eq!(code, 82, "{err}");
        assert_eq!(out, "#phonefarm_thermal_sample v1\nUPTIME\t12.50\n");
        assert!(!self.dir.join("candidate-marker").exists());
        assert!(err.starts_with("#phonefarm_thermal_rejection v1\n"));
        assert!(err.ends_with("#end_thermal_rejection\n"));
        assert!(err.len() <= 65536);
        assert_eq!(
            fs::read_to_string(self.dir.join("calls.tsv"))
                .unwrap()
                .lines()
                .filter(|s| *s == "values")
                .count(),
            1
        );
        err
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if !self.retained {
            let _ = fs::remove_dir_all(&self.dir);
        }
    }
}
fn row(s: &str, index: usize) -> Vec<&str> {
    s.lines()
        .find(|s| s.starts_with(&format!("SENSOR\t{index}\t")))
        .unwrap()
        .split('\t')
        .collect()
}

#[test]
fn successful_25_sensor_stdout_is_byte_exact_and_parser_unchanged() {
    let f = Fixture::new(25);
    let (code, out, err) = f.run(true, false, "success_25", HAL);
    assert_eq!(code, 0);
    assert!(err.is_empty());
    assert!(f.dir.join("candidate-marker").exists());
    let mut expected = "#phonefarm_thermal_sample v1\nUPTIME\t12.50\n".to_string();
    for (s, v) in f.profile.sensors.iter().zip(&f.values) {
        expected += &format!("TEMP\t{}\t{v}\n", s.path);
    }
    expected += &format!("#hal_begin\n{HAL}#hal_end\n#end\n");
    assert_eq!(out, expected);
    let paths = f
        .profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    assert_eq!(
        parse_sample(&out, &paths).unwrap()["readings"]
            .as_object()
            .unwrap()
            .len(),
        25
    );
}
#[test]
fn reference_band_failure_uses_same_captured_values_even_if_source_changes() {
    let mut f = Fixture::new(25);
    f.values[17] = "83000".into();
    let err = f.reject(true, true, "same_read_above_band");
    let r = row(&err, 18);
    assert_eq!(r.len(), 15);
    assert_eq!(
        &r[2..10],
        &[
            "/sys/class/thermal/thermal_zone17/temp",
            "milli_celsius",
            "1000",
            "78",
            "82",
            "90",
            "reference_band",
            "83000"
        ]
    );
    assert_eq!(&r[13..15], &["83", "value_above_high"]);
    assert!(err[..err.len().min(2000)]
        .contains("FAIL\tvalue_above_high\t18\t/sys/class/thermal/thermal_zone17/temp"));
    assert!(fs::read_to_string(f.dir.join("values.tsv"))
        .unwrap()
        .starts_with("80000\n"));
}
#[test]
fn missing_and_extra_records_reject_with_count_scope_not_invented_sensor_values() {
    let mut missing = Fixture::new(25);
    missing.values.pop();
    let err = missing.reject(true, false, "missing_record");
    assert!(err.contains("COUNTS\t25\t24\n"));
    assert!(err.contains("MAPPING\tordinal_only_count_mismatch\n"));
    let r = row(&err, 25);
    assert_eq!(r[9], "MISSING");
    assert_eq!(r[13], "UNKNOWN");
    assert_eq!(r[14], "missing_record");
    let mut extra = Fixture::new(25);
    extra.values.push("80000".into());
    let err = extra.reject(true, false, "extra_record");
    assert!(err.contains("COUNTS\t25\t26\n"));
    assert!(err.contains("FAIL\tvalue_count_mismatch\n"));
    assert_eq!(
        err.lines().filter(|s| s.starts_with("SENSOR\t")).count(),
        25
    );
}
#[test]
fn nonfinite_and_invalid_raw_do_not_invent_normalized_temperature() {
    for token in ["NaN", "Infinity", "-inf", "80\t000", "bad\u{1}raw"] {
        let mut f = Fixture::new(25);
        f.values[0] = token.into();
        let err = f.reject(true, false, "invalid_numeric");
        let r = row(&err, 1);
        assert_eq!(r.len(), 15);
        assert_eq!(r[13], "UNKNOWN");
        assert_eq!(r[14], "raw_format_invalid");
        if token.contains('\t') {
            assert!(r[9].contains("\\t"));
        }
        if token.contains('\u{1}') {
            assert_eq!(r[12], "1");
        }
    }
}
#[test]
fn absolute_cap_and_lower_band_retain_the_original_boundaries() {
    let mut cap = Fixture::new(25);
    cap.values[0] = "91000".into();
    let err = cap.reject(false, false, "above_absolute_cap");
    let r = row(&err, 1);
    assert_eq!(&r[5..9], &["-50", "90", "90", "absolute_cap"]);
    assert_eq!(r[14], "value_above_high");
    let mut low = Fixture::new(25);
    low.values[0] = "77000".into();
    assert_eq!(
        row(&low.reject(true, false, "below_band"), 1)[14],
        "value_below_low"
    );
}

#[test]
fn recorded_effective_band_preserves_the_actual_gate_number() {
    let mut f = Fixture::new(25);
    f.reference
        .bands
        .get_mut(&f.profile.sensors[0].path)
        .unwrap()
        .high_c = 83.12345678901234;
    f.values[0] = "83123.456790".into();
    let err = f.reject(true, false, "effective_band_precision");
    let r = row(&err, 1);
    let gate_high = format!(
        "{:.10}",
        f.reference.bands[&f.profile.sensors[0].path].high_c
    )
    .parse::<f64>()
    .unwrap();
    assert_eq!(r[6].parse::<f64>().unwrap(), gate_high);
    assert_eq!(r[14], "value_above_high");
}
#[test]
fn malformed_prefix_is_bounded_for_maximum_sensor_set() {
    let mut f = Fixture::new(128);
    f.values = vec!["\\".repeat(4096); 128];
    let err = f.reject(true, false, "maximum_bounded_prefix");
    assert_eq!(
        err.lines().filter(|s| s.starts_with("SENSOR\t")).count(),
        128
    );
    for line in err.lines().filter(|s| s.starts_with("SENSOR\t")) {
        let r = line.split('\t').collect::<Vec<_>>();
        assert_eq!(r.len(), 15);
        assert!(r[9].len() <= 128);
        assert_eq!(r[10], "4096");
        assert_eq!(r[11], "1");
    }
}
#[test]
fn type_and_hal_rejection_still_stop_before_candidate_and_no_success_end() {
    let f = Fixture::new(25);
    let (code, out, err) = f.run(
        true,
        false,
        "hal_severe",
        &HAL.replace("Status: 0", "Status: 3"),
    );
    assert_eq!(code, 83);
    assert!(out.contains("Thermal Status: 3\n"));
    assert!(!out.ends_with("#end\n"));
    assert!(!err.contains("thermal_rejection"));
    assert!(!f.dir.join("candidate-marker").exists());
    let mut t = Fixture::new(25);
    t.profile.sensors[0].sensor_type = "expected_type".into();
    // Actual observed type differs from frozen identity before reading values.
    t.tool("cat","printf 'cpu-0\\n'; i=1; while [ \"$i\" -lt 24 ]; do printf 'cpu-%s\\n' \"$i\"; i=$((i+1)); done\n");
    let (code, out, err) = t.run(true, false, "type_changed", HAL);
    assert_eq!(code, 84);
    assert!(!out.ends_with("#end\n"));
    assert!(err.is_empty());
    assert!(!t.dir.join("candidate-marker").exists());
}
