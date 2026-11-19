//! Configured lock and recovery paths. Reading status never creates directories.
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Serialize)]
pub(crate) struct Storage {
    schema_version: u32,
    pub state_dir: Option<PathBuf>,
    pub devlock_dir: Option<PathBuf>,
    pub device_lease_dir: Option<PathBuf>,
    pub error: Option<String>,
}

// Resolve existing ancestors too: macOS /var and /private/var must bind one target.
pub(crate) fn absolute(path: &Path) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(format!(
            "lease storage path must be absolute: {}",
            path.display()
        ));
    }
    let path = std::path::absolute(path).map_err(|e| e.to_string())?;
    let mut existing = path.as_path();
    let mut missing = Vec::new();
    loop {
        match fs::symlink_metadata(existing) {
            Ok(_) => break,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                missing.push(
                    existing
                        .file_name()
                        .ok_or("storage path has no existing ancestor")?,
                );
                existing = existing.parent().ok_or("storage path parent missing")?;
            }
            Err(e) => return Err(format!("storage path {}: {e}", existing.display())),
        }
    }
    let mut result = existing.canonicalize().map_err(|e| e.to_string())?;
    for component in missing.into_iter().rev() {
        result.push(component);
    }
    Ok(result)
}

fn configured_state_dir() -> Option<PathBuf> {
    std::env::var_os("PHONEFARM_STATE_DIR")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|p| PathBuf::from(p).join(".local/state/phonefarm"))
        })
}

fn state_path(path: &Path) -> Result<PathBuf, String> {
    let path = absolute(path)?;
    match fs::metadata(&path) {
        Ok(m) if m.is_dir() => Ok(path),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(path),
        Ok(_) => Err(format!("state path is not a directory: {}", path.display())),
        Err(e) => Err(format!("state path {}: {e}", path.display())),
    }
}

/// Resolve recovery and lease storage identically, without creating directories.
pub(crate) fn state_root() -> Result<PathBuf, String> {
    state_path(
        &configured_state_dir()
            .ok_or("PHONEFARM_STATE_DIR or HOME is required for persistent state storage")?,
    )
}

impl Storage {
    fn from_paths(state: Option<PathBuf>, farm: Option<PathBuf>, devices: Option<PathBuf>) -> Self {
        let mut storage = Self {
            schema_version: 1,
            state_dir: None,
            devlock_dir: None,
            device_lease_dir: None,
            error: None,
        };
        let paths = (|| -> Result<(), String> {
            storage.state_dir = state.as_deref().map(state_path).transpose()?;
            storage.devlock_dir = Some(match farm {
                Some(path) => state_path(&path)?,
                None => storage
                    .state_dir
                    .as_ref()
                    .ok_or("PHONEFARM_STATE_DIR or HOME is required for lock storage")?
                    .join("locks"),
            });
            storage.device_lease_dir = Some(match devices {
                Some(path) => state_path(&path)?,
                None => storage.devlock_dir.as_ref().unwrap().join("devices"),
            });
            Ok(())
        })();
        storage.error = paths.err();
        storage
    }
    pub(crate) fn roots(&self) -> Result<(PathBuf, PathBuf), String> {
        if let Some(error) = &self.error {
            return Err(error.clone());
        }
        Ok((
            self.device_lease_dir.clone().unwrap(),
            self.devlock_dir.clone().unwrap(),
        ))
    }
    pub(crate) fn custom(path: &Path) -> Self {
        Self::from_paths(None, Some(path.to_owned()), None)
    }
}
pub(crate) fn resolve() -> Storage {
    Storage::from_paths(
        configured_state_dir(),
        std::env::var_os("DEVLOCK_DIR").map(PathBuf::from),
        std::env::var_os("PHONEFARM_LEASE_DIR").map(PathBuf::from),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn paths_and_status_are_read_only_and_custom_overrides_are_explicit() {
        let root = std::env::temp_dir().join(format!("pf-paths-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let storage = Storage::from_paths(Some(root.join("state")), None, None);
        assert_eq!(
            storage.roots().unwrap(),
            (root.join("state/locks/devices"), root.join("state/locks"))
        );
        assert!(!root.join("state").exists());
        let custom =
            Storage::from_paths(None, Some(root.join("custom")), Some(root.join("devices")));
        assert_eq!(
            custom.roots().unwrap(),
            (root.join("devices"), root.join("custom"))
        );
        assert!(!root.join("custom").exists());
        assert!(Storage::custom(Path::new("relative"))
            .roots()
            .unwrap_err()
            .contains("absolute"));
        assert!(Storage::from_paths(
            Some(root.join("state")),
            None,
            Some("relative-devices".into())
        )
        .roots()
        .is_err());
        fs::write(root.join("not-directory"), b"preserve").unwrap();
        assert!(Storage::custom(&root.join("not-directory"))
            .roots()
            .is_err());
        assert_eq!(fs::read(root.join("not-directory")).unwrap(), b"preserve");
        fs::remove_dir_all(root).unwrap();
    }
}
