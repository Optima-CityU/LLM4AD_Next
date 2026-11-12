//! Raw Vulkan query evidence from the opt-in refbench sidecar. Queue timestamp
//! intervals describe GPU work submission/execution, never display/present timing.
use serde_json::Value;

pub const SOURCE: &str = "refbench_vulkan_queue_timestamp_interval";
pub const WARMUP_FRAMES: usize = 20;

pub struct Samples {
    pub intervals_ms: Vec<f64>,
    pub gpu_elapsed_ms: Vec<f64>,
}

pub fn parse(raw: &str, run_id: &str, frames: u32) -> Result<Samples, String> {
    let v: Value = serde_json::from_str(raw).map_err(|e| format!("refbench GPU timestamps: {e}"))?;
    if v["schema"] != "refbench/gpu-timestamps@1" || v["run_id"] != run_id
        || v["frames_config"].as_u64() != Some(frames as u64) || v["complete"] != true {
        return Err("refbench GPU timestamp identity/completion mismatch; rebuild the Mali refbench APK".into());
    }
    let bits = v["timestamp_valid_bits"].as_u64().filter(|n| (1..=64).contains(n))
        .ok_or("missing Vulkan timestamp valid bits")?;
    let period = v["timestamp_period_ns"].as_f64().filter(|n| n.is_finite() && *n > 0.0)
        .ok_or("missing Vulkan timestamp period")?;
    let rows = v["samples"].as_array().ok_or("missing GPU timestamp samples")?;
    if rows.len() != frames as usize || rows.len() < WARMUP_FRAMES + 9 {
        return Err("incomplete/insufficient refbench GPU timestamp frames".into());
    }
    let mask = if bits == 64 { u64::MAX } else { (1u64 << bits) - 1 };
    let elapsed = |start: u64, end: u64| -> Result<f64, String> {
        let delta = end.wrapping_sub(start) & mask;
        let ms = delta as f64 * period / 1_000_000.0;
        if delta == 0 || delta > mask / 2 || !ms.is_finite() || ms <= 0.0 {
            Err("invalid/nonmonotonic Vulkan timestamp interval".into())
        } else { Ok(ms) }
    };
    let mut samples = Samples { intervals_ms: Vec::new(), gpu_elapsed_ms: Vec::new() };
    let mut previous = None;
    for (frame, row) in rows.iter().enumerate() {
        let row = row.as_array().filter(|r| r.len() == 3).ok_or("bad Vulkan timestamp tuple")?;
        if row[0].as_u64() != Some(frame as u64) {
            return Err("Vulkan timestamp frame order/gap mismatch".into());
        }
        let start = row[1].as_u64().ok_or("missing GPU start tick")? & mask;
        let end = row[2].as_u64().ok_or("missing GPU end tick")? & mask;
        let duration = elapsed(start, end)?;
        if frame >= WARMUP_FRAMES {
            samples.gpu_elapsed_ms.push(duration);
            if let Some(previous) = previous { samples.intervals_ms.push(elapsed(previous, start)?); }
            previous = Some(start);
        }
    }
    Ok(samples)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> Value {
        // A 36-bit device clock wraps between frames, as can happen on mobile GPUs.
        let mask = (1u64 << 36) - 1;
        let ticks = (0..32u64).map(|i| {
            let start = (mask - 50_000_000 + i * 16_000_000) & mask;
            json!([i, start, (start + 2_000_000) & mask])
        }).collect::<Vec<_>>();
        json!({"schema":"refbench/gpu-timestamps@1","run_id":"gpuop_A",
            "frames_config":32,"complete":true,"timestamp_valid_bits":36,
            "timestamp_period_ns":1.0,"samples":ticks})
    }

    #[test]
    fn mali_gpu_timestamps_keep_queue_scope_and_handle_clock_wrap() {
        let s = parse(&fixture().to_string(), "gpuop_A", 32).unwrap();
        assert_eq!(s.intervals_ms, vec![16.0; 11]);
        assert_eq!(s.gpu_elapsed_ms, vec![2.0; 12]);
    }

    #[test]
    fn mali_gpu_timestamps_reject_stale_partial_and_reordered_runs() {
        let v = fixture();
        assert!(parse(&v.to_string(), "gpuop_B", 32).is_err());
        for key in ["complete", "timestamp_valid_bits", "timestamp_period_ns"] {
            let mut bad = v.clone();
            bad[key] = json!(false);
            assert!(parse(&bad.to_string(), "gpuop_A", 32).is_err());
        }
        let mut bad = v.clone();
        bad["samples"][24][0] = json!(23);
        assert!(parse(&bad.to_string(), "gpuop_A", 32).is_err());
        let mut bad = v.clone();
        bad["samples"][24][2] = bad["samples"][24][1].clone();
        assert!(parse(&bad.to_string(), "gpuop_A", 32).is_err());
        let mut bad = v;
        bad["samples"].as_array_mut().unwrap().pop();
        assert!(parse(&bad.to_string(), "gpuop_A", 32).is_err());
    }
}
