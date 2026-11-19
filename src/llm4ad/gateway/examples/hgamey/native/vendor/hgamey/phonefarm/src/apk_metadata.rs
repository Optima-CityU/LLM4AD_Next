//! Host-only single-base APK inspection. PackageInstaller remains the signature verifier.
use crate::{devlease::Lease, game_deploy::Asset};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{File, Metadata};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

pub(crate) fn tool() -> Option<PathBuf> {
    fn executable(path: PathBuf) -> Option<PathBuf> {
        if !path.is_absolute() {
            return None;
        }
        let path = path.canonicalize().ok()?;
        let info = path.metadata().ok()?;
        (info.is_file() && info.mode() & 0o111 != 0).then_some(path)
    }
    if let Some(path) = std::env::var_os("AAPT2_BIN") {
        // A broken explicit configuration must not silently choose another tool.
        return executable(PathBuf::from(path));
    }
    std::env::split_paths(&std::env::var_os("PATH")?)
        .filter(|path| path.is_absolute())
        .find_map(|path| executable(path.join("aapt2")))
}

fn identity(info: &Metadata) -> (u64, u64, u64, i64, i64, i64, i64) {
    (
        info.dev(),
        info.ino(),
        info.len(),
        info.mtime(),
        info.mtime_nsec(),
        info.ctime(),
        info.ctime_nsec(),
    )
}

fn unchanged(asset: &Asset, held: &File, before: &Metadata) -> Result<(), String> {
    let current = crate::game_deploy::source_file(asset)?;
    if identity(before) != identity(&held.metadata().map_err(|e| e.to_string())?)
        || identity(before) != identity(&current.metadata().map_err(|e| e.to_string())?)
    {
        return Err("APK cache source changed during metadata inspection".into());
    }
    Ok(())
}

pub(crate) fn inspect(lease: &Lease, dir: &Path, asset: &Asset) -> Result<Value, String> {
    let program = tool().ok_or(
        "APK metadata unavailable: configure an absolute executable AAPT2_BIN or explicit PATH",
    )?;
    let held = crate::game_deploy::source_file(asset)?;
    let before = held.metadata().map_err(|e| e.to_string())?;
    let path = asset.path.to_str().ok_or("APK cache path must be UTF-8")?;
    let mut outputs = Vec::new();
    for (operation, arguments) in [
        ("apk_badging", vec!["dump", "badging", path]),
        (
            "apk_manifest",
            vec!["dump", "xmltree", "--file", "AndroidManifest.xml", path],
        ),
    ] {
        unchanged(asset, &held, &before)?;
        let args: Vec<String> = arguments.iter().map(|s| (*s).to_string()).collect();
        let output = crate::lease_command::run(
            lease,
            &program,
            &args,
            dir,
            operation,
            Duration::from_secs(30),
        )?;
        unchanged(asset, &held, &before)?;
        if output.code != 0 {
            return Err(format!(
                "{operation} failed (exit {}); raw output {}",
                output.code,
                output.stdout_path.display()
            ));
        }
        // lease_command preserves original bytes; reject lossy UTF-8 interpretation.
        if output.stdout.contains('\u{fffd}') || output.stdout.contains('\0') {
            return Err(format!(
                "{operation} output cannot be interpreted; raw output {}",
                output.stdout_path.display()
            ));
        }
        outputs.push(output);
    }
    let mut value = parse(&outputs[0].stdout, &outputs[1].stdout)
        .map_err(|error| format!("{error}; raw APK metadata: {}", dir.display()))?;
    value["evidence"] = json!({
        "badging": {"stdout": outputs[0].stdout_path, "stderr": outputs[0].stdout_path.with_extension("stderr")},
        "xmltree": {"stdout": outputs[1].stdout_path, "stderr": outputs[1].stdout_path.with_extension("stderr")}
    });
    Ok(value)
}

fn quoted_fields(mut input: &str) -> Result<BTreeMap<String, String>, String> {
    let mut result = BTreeMap::new();
    while !input.trim().is_empty() {
        input = input.trim_start();
        let (key, rest) = input
            .split_once("='")
            .ok_or("cannot interpret APK package badging")?;
        if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
            return Err("cannot interpret APK package field".into());
        }
        let (value, tail) = rest
            .split_once('\'')
            .ok_or("unterminated APK package field")?;
        if result.insert(key.to_string(), value.to_string()).is_some() {
            return Err("duplicate APK package field".into());
        }
        if !tail.is_empty() && !tail.starts_with(char::is_whitespace) {
            return Err("cannot interpret APK package field suffix".into());
        }
        input = tail;
    }
    Ok(result)
}

fn decimal(text: &str, field: &str) -> Result<u64, String> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("{field} must be an explicit numeric APK value"));
    }
    text.parse().map_err(|_| format!("{field} is out of range"))
}

fn tree_string(raw: &str) -> Result<String, String> {
    let value = raw.split(" (Raw:").next().unwrap_or(raw).trim();
    serde_json::from_str::<String>(value)
        .map_err(|_| "cannot interpret Manifest string value".into())
}

fn tree_number(raw: &str) -> Result<u64, String> {
    let value = raw.split(" (Raw:").next().unwrap_or(raw).trim();
    for prefix in ["(type 0x10)0x", "(type 0x11)0x"] {
        if let Some(hex) = value.strip_prefix(prefix) {
            return u64::from_str_radix(hex, 16).map_err(|_| "invalid Manifest integer".into());
        }
    }
    if value.starts_with('"') {
        decimal(&tree_string(value)?, "Manifest integer")
    } else {
        decimal(value, "Manifest integer")
    }
}

fn tree_false(raw: &str) -> Result<bool, String> {
    match raw.trim() {
        "(type 0x12)0x0" | "false" | "\"false\" (Raw: \"false\")" => Ok(true),
        "(type 0x12)0xffffffff" | "(type 0x12)0x1" | "true" | "\"true\" (Raw: \"true\")" => {
            Ok(false)
        }
        _ => Err("cannot interpret Manifest boolean".into()),
    }
}

fn parse(badging: &str, tree: &str) -> Result<Value, String> {
    let packages: Vec<_> = badging
        .lines()
        .filter_map(|s| s.strip_prefix("package: "))
        .collect();
    if packages.len() != 1 {
        return Err("APK requires exactly one package badging record".into());
    }
    let fields = quoted_fields(packages[0])?;
    if fields
        .keys()
        .any(|key| matches!(key.as_str(), "split" | "isFeatureSplit" | "configForSplit"))
        || badging
            .lines()
            .any(|line| line.starts_with("uses-split:") || line.starts_with("uses-split "))
    {
        return Err("split APKs are unsupported".into());
    }
    let package = fields.get("name").ok_or("APK package name missing")?;
    if !crate::graylayer::valid_package(package) {
        return Err("invalid APK package name".into());
    }
    let version = decimal(
        fields.get("versionCode").ok_or("APK versionCode missing")?,
        "versionCode",
    )?;
    let sdks: Vec<_> = badging
        .lines()
        .filter_map(|s| {
            s.strip_prefix("minSdkVersion:'")
                .or_else(|| s.strip_prefix("sdkVersion:'"))
        })
        .collect();
    if sdks.len() != 1 {
        return Err("APK numeric minSdkVersion missing or ambiguous".into());
    }
    let min_sdk = decimal(
        sdks[0]
            .strip_suffix('\'')
            .ok_or("invalid sdkVersion badging")?,
        "minSdkVersion",
    )?;
    if min_sdk == 0 {
        return Err("APK minSdkVersion must be positive".into());
    }
    let native: Vec<_> = badging
        .lines()
        .filter_map(|s| s.strip_prefix("native-code:"))
        .collect();
    if native.len() > 1 {
        return Err("ambiguous APK native ABI metadata".into());
    }
    let mut abis = BTreeSet::new();
    if let Some(line) = native.first() {
        for item in line.split_whitespace() {
            let abi = item
                .strip_prefix('\'')
                .and_then(|s| s.strip_suffix('\''))
                .ok_or("cannot interpret native-code badging")?;
            if !matches!(
                abi,
                "armeabi"
                    | "armeabi-v7a"
                    | "arm64-v8a"
                    | "x86"
                    | "x86_64"
                    | "mips"
                    | "mips64"
                    | "riscv64"
            ) || !abis.insert(abi.to_string())
            {
                return Err("unknown or duplicate APK native ABI".into());
            }
        }
        if abis.is_empty() {
            return Err("empty native-code badging".into());
        }
    }

    // Parse aapt2's text tree, not binary AXML. Keep element context so an unrelated
    // activity attribute cannot stand in for the manifest/application declarations.
    let mut namespaces = BTreeMap::new();
    let mut elements: Vec<(usize, String)> = Vec::new();
    let mut seen = BTreeSet::new();
    let (mut root_count, mut application_count) = (0, 0);
    let (mut xml_package, mut xml_version, mut xml_major, mut xml_sdk) = (None, None, None, None);
    for line in tree.lines().filter(|line| !line.trim().is_empty()) {
        let indent = line.len() - line.trim_start().len();
        let line = line.trim();
        if let Some(namespace) = line.strip_prefix("N: ") {
            let (prefix, uri) = namespace
                .split_once('=')
                .ok_or("cannot interpret Manifest namespace")?;
            let uri = uri.split(" (line=").next().unwrap_or(uri);
            if namespaces
                .insert(prefix.to_string(), uri.to_string())
                .is_some()
            {
                return Err("ambiguous Manifest namespace".into());
            }
        } else if let Some(element) = line.strip_prefix("E: ") {
            let name = element
                .split_whitespace()
                .next()
                .ok_or("missing Manifest element")?;
            while elements.last().is_some_and(|(depth, _)| *depth >= indent) {
                elements.pop();
            }
            if elements.is_empty() {
                if name != "manifest" {
                    return Err("APK XML root is not manifest".into());
                }
                root_count += 1;
            }
            if name == "uses-split" {
                return Err("uses-split APKs are unsupported".into());
            }
            if name == "application" && elements.len() == 1 {
                application_count += 1;
            }
            elements.push((indent, name.to_string()));
        } else if let Some(attribute) = line.strip_prefix("A: ") {
            let (key, raw) = attribute
                .split_once('=')
                .ok_or("cannot interpret Manifest attribute")?;
            let key = key.split('(').next().unwrap_or(key);
            let key = if let Some(name) =
                key.strip_prefix("http://schemas.android.com/apk/res/android:")
            {
                format!("android:{name}")
            } else if let Some((prefix, name)) = key.split_once(':') {
                if namespaces.get(prefix).map(String::as_str)
                    == Some("http://schemas.android.com/apk/res/android")
                {
                    format!("android:{name}")
                } else {
                    key.to_string()
                }
            } else {
                key.to_string()
            };
            let names: Vec<_> = elements.iter().map(|(_, name)| name.as_str()).collect();
            let root = names == ["manifest"];
            let application = names == ["manifest", "application"];
            let sdk = names == ["manifest", "uses-sdk"];
            if (root || application)
                && matches!(
                    key.as_str(),
                    "split"
                        | "configForSplit"
                        | "android:isFeatureSplit"
                        | "android:configForSplit"
                        | "android:isSplitRequired"
                        | "android:sharedUserId"
                        | "android:sharedUserMaxSdkVersion"
                )
            {
                return Err("split or shared-UID APK declarations are unsupported".into());
            }
            let relevant =
                root && matches!(
                    key.as_str(),
                    "package" | "android:versionCode" | "android:versionCodeMajor"
                ) || application && key == "android:testOnly"
                    || sdk && key == "android:minSdkVersion";
            if relevant && !seen.insert((names.join("/"), key.clone())) {
                return Err("duplicate critical Manifest attribute".into());
            }
            match (root, application, sdk, key.as_str()) {
                (true, _, _, "package") => xml_package = Some(tree_string(raw)?),
                (true, _, _, "android:versionCode") => xml_version = Some(tree_number(raw)?),
                (true, _, _, "android:versionCodeMajor") => xml_major = Some(tree_number(raw)?),
                (_, true, _, "android:testOnly") if !tree_false(raw)? => {
                    return Err("test-only APKs are unsupported".into())
                }
                (_, _, true, "android:minSdkVersion") => xml_sdk = Some(tree_number(raw)?),
                _ => {}
            }
        } else {
            return Err("unrecognized aapt2 Manifest tree output".into());
        }
    }
    if root_count != 1
        || application_count != 1
        || xml_package.as_ref() != Some(package)
        || xml_sdk != Some(min_sdk)
    {
        return Err(
            "APK package/minSdk/application metadata is missing or disagrees with Manifest".into(),
        );
    }
    let low = xml_version.ok_or("Manifest versionCode missing")?;
    let major = xml_major.unwrap_or(0);
    if low > u32::MAX as u64 || major > i32::MAX as u64 {
        return Err("Manifest versionCode is out of range".into());
    }
    let long_version = (major << 32) | low;
    let badge_major = fields
        .get("versionCodeMajor")
        .map(|s| decimal(s, "versionCodeMajor"))
        .transpose()?;
    if long_version == 0
        || (version != long_version && !(version == low && badge_major == Some(major)))
        || badge_major.is_some_and(|m| m != major)
    {
        return Err("APK versionCode disagrees with Manifest".into());
    }
    Ok(
        json!({"package": package, "version_code": long_version, "min_sdk": min_sdk,
        "abis": abis, "single_base_apk": true, "shared_uid": false, "test_only": false,
        "signature_verification": "package_installer_at_commit",
        "container_audit": "not_performed", "apex_support": false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    // aapt2 build-tools 35.0.0 output from the new 807-byte QA fixture.
    const BADGING: &str = "package: name='com.example.installfixture' versionCode='17' versionName='fixture-17' platformBuildVersionName='15' platformBuildVersionCode='35' compileSdkVersion='35' compileSdkVersionCodename='15'\nminSdkVersion:'23'\n";
    const TREE: &str = "N: android=http://schemas.android.com/apk/res/android (line=1)\n  E: manifest (line=1)\n    A: http://schemas.android.com/apk/res/android:versionCode(0x0101021b)=17\n    A: package=\"com.example.installfixture\" (Raw: \"com.example.installfixture\")\n      E: uses-sdk (line=2)\n        A: http://schemas.android.com/apk/res/android:minSdkVersion(0x0101020c)=23\n      E: application (line=3)\n        A: http://schemas.android.com/apk/res/android:hasCode(0x0101000c)=false\n        A: http://schemas.android.com/apk/res/android:testOnly(0x01010272)=false\n";
    #[test]
    fn metadata_cross_checks_manifest_and_leaves_signature_to_installer() {
        let value = parse(BADGING, TREE).unwrap();
        assert_eq!(value["version_code"], 17);
        assert_eq!(value["abis"], json!([]));
        assert_eq!(
            value["signature_verification"],
            "package_installer_at_commit"
        );
        assert!(parse(
            BADGING,
            &TREE.replace("versionCode(0x0101021b)=17", "versionCode(0x0101021b)=18")
        )
        .is_err());
        assert!(parse(
            BADGING,
            &TREE.replace(
                "testOnly(0x01010272)=false",
                "testOnly(0x01010272)=@0x7f010001"
            )
        )
        .is_err());
    }
    #[test]
    fn metadata_rejects_split_shared_uid_and_test_only() {
        assert!(parse(
            &BADGING.replace("versionName='fixture-17'", "split='feature'"),
            TREE
        )
        .is_err());
        for declaration in ["android:sharedUserId=\"shared\"", "split=\"config.en\""] {
            assert!(parse(
                BADGING,
                &TREE.replace(
                    "      E: uses-sdk",
                    &format!("    A: {declaration}\n      E: uses-sdk")
                )
            )
            .is_err());
        }
        assert!(parse(BADGING, &TREE.replace("E: uses-sdk", "E: uses-split")).is_err());
        assert!(parse(
            BADGING,
            &TREE.replace(
                "testOnly(0x01010272)=false",
                "isSplitRequired(0x01010591)=true"
            )
        )
        .is_err());
        assert!(parse(
            BADGING,
            &TREE.replace("testOnly(0x01010272)=false", "testOnly(0x01010272)=true")
        )
        .is_err());
    }
}
