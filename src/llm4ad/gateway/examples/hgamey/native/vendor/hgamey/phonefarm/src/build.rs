use std::env;

fn main() {
    for name in [
        "HGAMEY_BUILD_REVISION",
        "HGAMEY_BUILD_DIRTY",
        "TARGET",
        "PROFILE",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    println!("cargo:rerun-if-changed=build.rs");
    let revision = env::var("HGAMEY_BUILD_REVISION")
        .ok()
        .filter(|v| !v.is_empty() && v.len() <= 256 && !v.chars().any(char::is_control))
        .unwrap_or_default();
    let dirty = match env::var("HGAMEY_BUILD_DIRTY").as_deref() {
        Ok("true" | "1") => "true",
        Ok("false" | "0") => "false",
        _ => "",
    };
    println!("cargo:rustc-env=HGAMEY_RUNTIME_REVISION={revision}");
    println!("cargo:rustc-env=HGAMEY_RUNTIME_DIRTY={dirty}");
    for (source, name) in [
        ("TARGET", "HGAMEY_RUNTIME_TARGET"),
        ("PROFILE", "HGAMEY_RUNTIME_PROFILE"),
    ] {
        let value = env::var(source).unwrap_or_default();
        if !value.contains(['\n', '\r']) {
            println!("cargo:rustc-env={name}={value}");
        }
    }
}
