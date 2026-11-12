//! Three owned image checkpoints, not a navigation or comparison engine.
use serde_json::{json, Value};
use std::io::{BufRead, Cursor, Seek};

const SCHEMA: &str = "phonefarm/gray-quality@1";

pub(crate) fn new_manifest(d: &Value, label: &str, path: &str, at: &str) -> Value {
    json!({"schema":SCHEMA,"session_id":d["session_id"],"job_id":d["job_id"],
        "serial":d["serial"],"package":d["package"],"mode":d["mode"],"label":label,"path":path,
        "phase":null,"stages":{"begin":null,"checkpoint":null,"finish":null},
        "active_attempt":null,"last_attempt":null,"created_at":at,"updated_at":at})
}

pub(crate) fn validate(m: &Value, d: &Value, label: &str, path: &str) -> Result<(), String> {
    if m["schema"] != SCHEMA
        || m["label"] != label
        || m["path"] != path
        || ["session_id", "job_id", "serial", "package", "mode"]
            .iter()
            .any(|k| m[*k] != d[*k])
    {
        return Err("quality manifest does not belong to this label/session".into());
    }
    let count = match m["phase"].as_str() {
        None if m["phase"].is_null() => 0,
        Some("begun") => 1,
        Some("checkpointed") => 2,
        Some("collected") => 3,
        _ => return Err("unknown quality phase".into()),
    };
    if !m["stages"].is_object()
        || m.get("active_attempt").is_none()
        || m.get("last_attempt").is_none()
    {
        return Err("quality manifest is missing its stage/attempt records".into());
    }
    for (index, op) in ["begin", "checkpoint", "finish"].iter().enumerate() {
        let stage = &m["stages"][*op];
        if index < count {
            if stage["op"] != *op
                || stage["status"] != "completed"
                || !stage["image"].is_object()
                || !stage["preview"].is_object()
                || (*op == "finish" && !stage["marker"].is_object())
            {
                return Err("quality successful stage is incomplete".into());
            }
        } else if !stage.is_null() {
            return Err("quality phases are out of order".into());
        }
    }
    Ok(())
}

pub(crate) fn start_attempt(m: &mut Value, r: &Value, path: &str, at: &str) -> Result<(), String> {
    if !m["active_attempt"].is_null() {
        return Err("quality attempt is unresolved; do not replay it".into());
    }
    let op = r["params"]["op"]
        .as_str()
        .ok_or("missing quality operation")?;
    let allowed = match op {
        "begin" => m["phase"].is_null(),
        "checkpoint" => m["phase"] == "begun",
        "finish" => m["phase"] == "checkpointed",
        _ => false,
    };
    if !allowed {
        return Err("quality requires begin, checkpoint, finish in order; successful stages cannot be replaced".into());
    }
    m["active_attempt"] = json!({"op":op,"request_id":r["request_id"],"actor_id":r["actor_id"],
        "control_epoch":r["control_epoch"],"status":"started","started_at":at,"finished_at":null,
        "image":null,"preview":null,"marker":null,"error":null,"path":path});
    m["updated_at"] = json!(at);
    Ok(())
}

pub(crate) fn finish_attempt(
    m: &mut Value,
    error: Option<Value>,
    at: &str,
) -> Result<Value, String> {
    let mut attempt = m["active_attempt"].clone();
    let op = attempt["op"]
        .as_str()
        .ok_or("quality has no active attempt")?
        .to_owned();
    if error.is_none()
        && (!attempt["image"].is_object()
            || !attempt["preview"].is_object()
            || (op == "finish" && !attempt["marker"].is_object()))
    {
        return Err("quality cannot complete without its required image/preview/marker".into());
    }
    attempt["status"] = json!(if error.is_some() {
        "failed"
    } else {
        "completed"
    });
    attempt["error"] = error.unwrap_or(Value::Null);
    attempt["finished_at"] = json!(at);
    if attempt["status"] == "completed" {
        m["stages"][op.as_str()] = attempt.clone();
        m["phase"] = json!(match op.as_str() {
            "begin" => "begun",
            "checkpoint" => "checkpointed",
            "finish" => "collected",
            _ => return Err("unknown quality operation".into()),
        });
    }
    m["last_attempt"] = attempt.clone();
    m["active_attempt"] = Value::Null;
    m["updated_at"] = json!(at);
    Ok(attempt)
}

pub(crate) fn summary(m: &Value) -> Value {
    json!({"label":m["label"],"path":m["path"],"phase":m["phase"],"stages":m["stages"],
        "active_attempt":m["active_attempt"],"last_attempt":m["last_attempt"],"updated_at":m["updated_at"]})
}

pub(crate) fn read_png<R: BufRead + Seek>(source: R) -> Result<image::DynamicImage, String> {
    let mut reader = image::ImageReader::new(source)
        .with_guessed_format()
        .map_err(|e| e.to_string())?;
    if reader.format() != Some(image::ImageFormat::Png) {
        return Err("capture is not PNG".into());
    }
    let mut limits = image::Limits::default();
    limits.max_alloc = Some(64 * 1024 * 1024);
    limits.max_image_width = Some(8192);
    limits.max_image_height = Some(8192);
    reader.limits(limits);
    let image = reader
        .decode()
        .map_err(|e| format!("PNG decode failed: {e}"))?;
    if image.width() == 0
        || image.height() == 0
        || u64::from(image.width()) * u64::from(image.height()) > 16 * 1024 * 1024
    {
        return Err("decoded image exceeds dimension limit".into());
    }
    Ok(image)
}

pub(crate) fn validate_marker(marker: &Value, package: &str) -> Result<(), String> {
    if marker["layer_loaded"] != true
        || marker["pkg"] != package
        || marker["readonly_stats"]["frames"].as_u64().is_none()
        || marker["readonly_stats"]["render_pass_begins"]
            .as_u64()
            .is_none()
    {
        return Err("quality finish marker does not identify the loaded target layer with valid frame/render-pass counters".into());
    }
    Ok(())
}

/// Preserve the historical even-coordinate RGB stride exactly, including odd
/// source dimensions. This preview never replaces the source PNG bytes.
pub(crate) fn half_preview(source: &image::DynamicImage) -> Result<(Vec<u8>, u32, u32), String> {
    let (width, height) = (source.width() / 2, source.height() / 2);
    if width == 0 || height == 0 {
        return Err("source is too small for a half-size preview".into());
    }
    let source = source.to_rgb8();
    let preview = image::RgbImage::from_fn(width, height, |x, y| *source.get_pixel(x * 2, y * 2));
    let mut encoded = Cursor::new(Vec::new());
    image::DynamicImage::ImageRgb8(preview)
        .write_to(&mut encoded, image::ImageFormat::Png)
        .map_err(|e| e.to_string())?;
    Ok((encoded.into_inner(), width, height))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn half_preview_keeps_even_rgb_pixels_and_odd_size_floor() {
        let source = image::RgbaImage::from_fn(5, 5, |x, y| {
            image::Rgba([x as u8, y as u8, (x + 10 * y) as u8, 17])
        });
        let (bytes, width, height) =
            half_preview(&image::DynamicImage::ImageRgba8(source)).unwrap();
        assert_eq!((width, height), (2, 2));
        let result = image::load_from_memory(&bytes).unwrap();
        assert_eq!(result.color(), image::ColorType::Rgb8);
        assert_eq!(
            result.to_rgb8().as_raw(),
            &vec![0, 0, 0, 2, 0, 2, 0, 2, 20, 2, 2, 22]
        );
    }
}
