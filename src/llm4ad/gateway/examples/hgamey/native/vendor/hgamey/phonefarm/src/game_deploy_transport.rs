//! Bounded game-transaction transport; each Session owns its intent and recovery state.
use crate::{devlease::Lease, lease_command::Output};
use serde_json::json;
use std::{
    path::{Path, PathBuf},
    time::Duration,
};

pub(crate) fn q(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

pub(crate) struct Remote {
    pub(crate) code: i32,
    pub(crate) stdout: String,
}

#[derive(Clone, Copy)]
pub(crate) enum ShellStderr {
    Separate,
    Merged,
}

pub(crate) struct TransportError {
    pub(crate) message: String,
    /// The caller must retain its recovery hold and persist its sticky flag.
    pub(crate) uncertain: bool,
}
impl TransportError {
    fn uncertain(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            uncertain: true,
        }
    }
}

pub(crate) struct Transport {
    adb: PathBuf,
    serial: String,
    token: String,
    dir: PathBuf,
    sequence: u64,
    stderr: ShellStderr,
    guarded: bool,
}
impl Transport {
    pub(crate) fn new(
        serial: &str,
        token: &str,
        dir: &Path,
        stderr: ShellStderr,
    ) -> Result<Self, String> {
        let adb = crate::device::locate_adb()
            .ok_or("ADB executable unavailable")?
            .into();
        Ok(Self {
            adb,
            serial: serial.into(),
            token: token.into(),
            dir: dir.into(),
            sequence: 0,
            stderr,
            guarded: false,
        })
    }

    pub(crate) fn guarded(serial: &str, token: &str, dir: &Path) -> Result<Self, String> {
        let mut transport = Self::new(serial, token, dir, ShellStderr::Separate)?;
        transport.guarded = true;
        Ok(transport)
    }

    pub(crate) fn raw(
        &mut self,
        lease: &Lease,
        operation: &str,
        args: Vec<String>,
        seconds: u64,
    ) -> Result<Output, TransportError> {
        let mut argv = vec!["-s".into(), self.serial.clone()];
        argv.extend(args);
        crate::confirmation_transport::journal(
            &self.dir,
            json!({"at":chrono::Utc::now().to_rfc3339(),"operation":operation,"event":"start"}),
        )
        .map_err(|message| TransportError {
            message,
            uncertain: false,
        })?;
        let execute = if self.guarded {
            crate::lease_command::run_guarded_transport
        } else {
            crate::lease_command::run
        };
        let output = execute(
            lease,
            &self.adb,
            &argv,
            &self.dir,
            operation,
            Duration::from_secs(seconds),
        )
        .map_err(TransportError::uncertain)?;
        crate::confirmation_transport::journal(
            &self.dir,
            json!({"at":chrono::Utc::now().to_rfc3339(),"operation":operation,"event":"exit",
                "code":output.code,"stdout":output.stdout_path.file_name()}),
        )
        .map_err(|e| {
            TransportError::uncertain(format!("command result could not be recorded: {e}"))
        })?;
        Ok(output)
    }

    pub(crate) fn shell(
        &mut self,
        lease: &Lease,
        operation: &str,
        command: &str,
        seconds: u64,
    ) -> Result<Remote, TransportError> {
        self.sequence += 1;
        // Recovery uses the original transaction token, not the newly acquired lease token.
        let terminal = format!("PF_GAME_DEPLOY_EXIT:{}:{}:", self.token, self.sequence);
        let redirect = match self.stderr {
            ShellStderr::Separate => "",
            ShellStderr::Merged => " 2>&1",
        };
        let wrapper = format!(
            "sh -c {}{redirect}; rc=$?; printf '\\n{}%s\\n' \"$rc\"; exit 0",
            q(command),
            terminal
        );
        let output = self.raw(lease, operation, vec!["shell".into(), wrapper], seconds)?;
        if output.code != 0 {
            return Err(TransportError::uncertain(
                "ADB shell did not confirm remote completion",
            ));
        }
        parse_terminal(&output.stdout, &terminal).map_err(TransportError::uncertain)
    }
}

pub(crate) fn parse_terminal(output: &str, terminal: &str) -> Result<Remote, String> {
    let normalized = output.replace("\r\n", "\n");
    let value = normalized.trim_end_matches('\n');
    let (body, last) = value
        .rsplit_once('\n')
        .ok_or("missing remote completion line")?;
    let code = last
        .strip_prefix(terminal)
        .and_then(|v| v.parse::<i32>().ok())
        .filter(|v| (0..=255).contains(v))
        .ok_or("invalid remote completion line")?;
    Ok(Remote {
        code,
        stdout: body.into(),
    })
}
