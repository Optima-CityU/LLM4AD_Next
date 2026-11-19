//! Finite input plans for owned gray eval; never an arbitrary script executor.
use serde::Serialize;
use serde_json::{json, Map, Value};

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(tag = "action", rename_all = "snake_case")]
enum Step {
    Sleep {
        ms: u64,
    },
    Tap {
        x: u32,
        y: u32,
    },
    Swipe {
        x: u32,
        y: u32,
        to_x: u32,
        to_y: u32,
        duration_ms: u64,
    },
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub(crate) struct Plan {
    pub seconds: u64,
    total_ms: u64,
    steps: Vec<Step>,
}

fn field<'a>(o: &'a Map<String, Value>, names: &[&str]) -> Result<Option<&'a Value>, String> {
    let values: Vec<_> = names.iter().filter_map(|k| o.get(*k)).collect();
    if values.len() > 1 {
        return Err(format!("duplicate field aliases: {}", names.join("/")));
    }
    Ok(values.first().copied())
}
fn integer(value: &Value, label: &str, max: u64) -> Result<u64, String> {
    value
        .as_u64()
        .filter(|n| *n <= max)
        .ok_or_else(|| format!("{label} must be an integer in 0..{max}"))
}
fn known(o: &Map<String, Value>, names: &[&str]) -> Result<(), String> {
    if let Some(k) = o.keys().find(|k| {
        !names.contains(&k.as_str()) && !["action", "a", "act", "op", "type"].contains(&k.as_str())
    }) {
        return Err(format!("unsupported finite action field: {k}"));
    }
    Ok(())
}
fn coord(o: &Map<String, Value>, names: &[&str]) -> Result<u32, String> {
    let v = field(o, names)?.ok_or_else(|| format!("missing {}", names[0]))?;
    Ok(integer(v, names[0], u32::MAX as u64)? as u32)
}
fn expand(
    values: &[Value],
    depth: usize,
    out: &mut Vec<Step>,
    total: &mut u64,
) -> Result<(), String> {
    if depth > 10 {
        return Err("finite script nesting exceeds 10".into());
    }
    for value in values {
        let o = value.as_object().ok_or("finite step must be an object")?;
        let action = field(o, &["action", "a", "act", "op", "type"])?
            .and_then(Value::as_str)
            .ok_or("action must be a string")?;
        let step = match action {
            "loop" | "repeat" => {
                known(o, &["count", "repeat", "steps", "actions", "body"])?;
                let count = field(o, &["count", "repeat"])?
                    .map(|v| integer(v, "count", 10000))
                    .transpose()?
                    .unwrap_or(1);
                let sub = field(o, &["steps", "actions", "body"])?
                    .and_then(Value::as_array)
                    .ok_or("loop steps must be an array")?;
                // Validate even a zero-repeat body; unsupported actions cannot hide here.
                let mut expanded = Vec::new();
                let mut subtotal = 0;
                expand(sub, depth + 1, &mut expanded, &mut subtotal)?;
                let added = expanded
                    .len()
                    .checked_mul(count as usize)
                    .ok_or("step count overflow")?;
                if out.len().checked_add(added).is_none_or(|n| n > 10000) {
                    return Err("finite script exceeds 10000 steps".into());
                }
                *total = total
                    .checked_add(subtotal.checked_mul(count).ok_or("duration overflow")?)
                    .ok_or("duration overflow")?;
                if *total > 600000 {
                    return Err("finite script exceeds 600000ms".into());
                }
                for _ in 0..count {
                    out.extend(expanded.iter().cloned());
                }
                continue;
            }
            "sleep" | "wait" => {
                known(
                    o,
                    &[
                        "ms", "sleep_ms", "wait_ms", "delay_ms", "sec", "s", "seconds",
                    ],
                )?;
                let ms = field(o, &["ms", "sleep_ms", "wait_ms", "delay_ms"])?;
                let sec = field(o, &["sec", "s", "seconds"])?;
                // Both supplied values must be valid, even though ms takes precedence.
                let sec = sec
                    .map(|v| {
                        v.as_f64()
                            .filter(|s| s.is_finite() && *s >= 0.0 && *s <= 60.0)
                            .map(|s| (s * 1000.0) as u64)
                            .ok_or("sec must be finite in 0..60")
                    })
                    .transpose()?;
                Step::Sleep {
                    ms: ms
                        .map(|v| integer(v, "ms", 60000))
                        .transpose()?
                        .or(sec)
                        .unwrap_or(1000),
                }
            }
            "tap" | "click" => {
                known(o, &["x", "y"])?;
                Step::Tap {
                    x: coord(o, &["x"])?,
                    y: coord(o, &["y"])?,
                }
            }
            "swipe" => {
                known(o, &["x", "y", "to_x", "x2", "to_y", "y2", "duration_ms"])?;
                let duration_ms = field(o, &["duration_ms"])?
                    .map(|v| integer(v, "duration_ms", 60000))
                    .transpose()?
                    .unwrap_or(350);
                if duration_ms == 0 {
                    return Err("swipe duration_ms must be positive".into());
                }
                Step::Swipe {
                    x: coord(o, &["x"])?,
                    y: coord(o, &["y"])?,
                    to_x: coord(o, &["to_x", "x2"])?,
                    to_y: coord(o, &["to_y", "y2"])?,
                    duration_ms,
                }
            }
            "exec" => {
                known(o, &["cmd"])?;
                let words: Vec<_> = o
                    .get("cmd")
                    .and_then(Value::as_str)
                    .ok_or("exec cmd must be a string")?
                    .split_whitespace()
                    .collect();
                if words.len() != 7 || words[..2] != ["input", "swipe"] {
                    return Err("only exact legacy input swipe is supported".into());
                }
                let nums = words[2..]
                    .iter()
                    .map(|s| {
                        if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
                            return Err("legacy swipe requires decimal integers".to_string());
                        }
                        s.parse::<u64>()
                            .map_err(|_| "legacy swipe integer overflow".to_string())
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                if nums[..4].iter().any(|n| *n > u32::MAX as u64) || !(1..=60000).contains(&nums[4])
                {
                    return Err("legacy swipe parameter out of range".into());
                }
                Step::Swipe {
                    x: nums[0] as u32,
                    y: nums[1] as u32,
                    to_x: nums[2] as u32,
                    to_y: nums[3] as u32,
                    duration_ms: nums[4],
                }
            }
            _ => return Err(format!("unsupported finite action: {action}")),
        };
        let ms = match step {
            Step::Sleep { ms } => ms,
            Step::Swipe { duration_ms, .. } => duration_ms,
            Step::Tap { .. } => 0,
        };
        *total = total.checked_add(ms).ok_or("duration overflow")?;
        if *total > 600000 {
            return Err("finite script exceeds 600000ms".into());
        }
        out.push(step);
        if out.len() > 10000 {
            return Err("finite script exceeds 10000 steps".into());
        }
    }
    Ok(())
}

pub(crate) fn parse(input: &Value, seconds: u64) -> Result<Plan, String> {
    if !(1..=594).contains(&seconds) {
        return Err("finite workload.seconds must be 1..594".into());
    }
    let values = if let Some(a) = input.as_array() {
        a
    } else {
        let o = input
            .as_object()
            .ok_or("finite script must be an array or steps object")?;
        if o.len() != 1 {
            return Err("finite script wrapper only accepts steps".into());
        }
        o.get("steps")
            .and_then(Value::as_array)
            .ok_or("steps must be an array")?
    };
    let mut plan = Plan {
        seconds,
        total_ms: 0,
        steps: Vec::new(),
    };
    expand(values, 0, &mut plan.steps, &mut plan.total_ms)?;
    if plan.steps.is_empty() || plan.total_ms < (6 + seconds) * 1000 {
        return Err("finite input duration must cover trace lead 6s plus workload.seconds".into());
    }
    Ok(plan)
}
impl Plan {
    pub fn control(input: Option<&Value>, seconds: u64) -> Result<Self, String> {
        if !(1..=120).contains(&seconds) { return Err("control sample seconds must be 1..120".into()); }
        let Some(input) = input else { return Self::idle(seconds); };
        let plan = parse(input, seconds)?;
        if plan.duration_ms() < (seconds + 10) * 1000 {
            return Err("control input must cover sample seconds plus 10 seconds without implicit padding".into());
        }
        Ok(plan)
    }
    /// Current-scene sampling owns a finite idle workload, never input.
    pub fn idle(seconds: u64) -> Result<Self, String> {
        if !(1..=120).contains(&seconds) { return Err("control sample seconds must be 1..120".into()); }
        let mut remaining = (seconds + 10) * 1000;
        let mut steps = Vec::new();
        while remaining > 0 {
            let ms = remaining.min(60000);
            steps.push(Step::Sleep { ms });
            remaining -= ms;
        }
        Ok(Self { seconds, total_ms: (seconds + 10) * 1000, steps })
    }
    pub fn duration_ms(&self) -> u64 {
        self.total_ms
    }
    pub fn to_value(&self) -> Value {
        json!({"schema":"phonefarm/gray-input-plan@1","seconds":self.seconds,"total_ms":self.total_ms,"steps":self.steps})
    }
    pub fn validate_screen(&self, w: u32, h: u32) -> Result<(), String> {
        if w == 0 || h == 0 {
            return Err("empty input screen dimensions".into());
        }
        for s in &self.steps {
            let valid = match s {
                Step::Sleep { .. } => true,
                Step::Tap { x, y } => *x < w && *y < h,
                Step::Swipe {
                    x, y, to_x, to_y, ..
                } => *x < w && *to_x < w && *y < h && *to_y < h,
            };
            if !valid {
                return Err(format!("input coordinate outside original {w}x{h} screen"));
            }
        }
        Ok(())
    }
    pub fn shell(&self) -> String {
        let constants = format!(
            "GRAY_BUDGET={}\nSTEPS={}; DECLARED_MS={}",
            self.total_ms.div_ceil(1000) + 30,
            self.steps.len(),
            self.total_ms
        );
        let mut text = String::new();
        for step in &self.steps {
            match step {
                Step::Sleep { ms } => {
                    text.push_str(&format!("sleep {}.{:03}\n", ms / 1000, ms % 1000))
                }
                Step::Tap { x, y } => text.push_str(&format!("input tap {x} {y}\n")),
                Step::Swipe {
                    x,
                    y,
                    to_x,
                    to_y,
                    duration_ms,
                } => text.push_str(&format!(
                    "input swipe {x} {y} {to_x} {to_y} {duration_ms}\n"
                )),
            }
        }
        include_str!("../loop_v1/tools/gray_input_workload.sh")
            .replace("# GRAY_PLAN_CONSTANTS", &constants)
            .replace("# GRAY_PLAN_ACTIONS", &text)
    }
}

pub(crate) fn sampler_shell() -> String {
    format!("#!/system/bin/sh\nset -eu\nDUR=${{1:?seconds}}; IVL=${{2:?interval}}; R=${{3:?round}}; TOKEN=${{4:?token}}; BOOT=${{5:?boot}}\nD=${{R%/*}}\n. \"$D/gray_child_guard.sh\"\ngray_child_begin sampler \"$((DUR+30))\"\ntrap 'code=$?; trap - EXIT INT TERM; gray_child_finish \"$code\" || exit 75; exit \"$code\"' EXIT\ntrap 'exit 130' INT\ntrap 'exit 143' TERM\n(\nset +e\n{}\n)\n",include_str!("../loop_v1/auto/sample_env.sh"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_plan_preserves_twelve_second_swipes_and_screen_bounds() {
        let input: Value = serde_json::from_str(include_str!(
            "../loop_v1/scripts/workload_spin_touch_v1.json"
        ))
        .unwrap();
        let p = parse(&input, 30).unwrap();
        assert_eq!(p.duration_ms(), 40000);
        assert_eq!(
            p.shell()
                .matches("input swipe 900 400 2000 400 12000")
                .count(),
            3
        );
        assert!(p.validate_screen(2400, 1080).is_ok());
        assert!(p.validate_screen(1080, 2400).is_err());
        assert_eq!(parse(&p.to_value()["steps"], 30).unwrap(), p);
    }
    #[test]
    fn rejects_unsafe_invalid_or_unbounded_plans() {
        for step in [
            json!({"action":"exec","cmd":"input swipe 1 2 3 4 12000; reboot"}),
            json!({"action":"gamepad_down","button":"a"}),
            json!({"action":"sleep","ms":"10000"}),
            json!({"action":"sleep","ms":60001}),
            json!({"action":"tap","x":1}),
            json!({"action":"swipe","x":1,"y":2,"x2":3,"y2":4,"duration_ms":0}),
            json!({"action":"loop","count":11,"steps":[{"action":"sleep","ms":60000}]}),
        ] {
            assert!(parse(&json!([step]), 1).is_err());
        }
        assert!(parse(&json!([{"action":"sleep","ms":6000}]), 1).is_err());
        assert!(parse(&json!([{"action":"sleep","ms":7000}]), 595).is_err());
        assert!(parse(&json!([{"action":"sleep","ms":7000,"sec":-1}]), 1).is_err());
        assert_eq!(Plan::control(None,1).unwrap(),Plan::idle(1).unwrap());
        assert!(Plan::control(Some(&Value::Null),1).is_err());
        let short=json!([{"action":"sleep","ms":7000}]);
        assert!(parse(&short,1).is_ok()); // Preserve the original eval contract.
        assert!(Plan::control(Some(&short),1).is_err());
        let input=json!([{"action":"sleep","ms":6000},{"action":"tap","x":10,"y":5},{"action":"sleep","ms":5000}]);
        let plan=Plan::control(Some(&input),1).unwrap();
        assert_eq!(plan.duration_ms(),11000);
        assert!(plan.validate_screen(11,6).is_ok());assert!(plan.validate_screen(10,6).is_err());
        assert!(Plan::control(None,121).is_err());
    }
}
