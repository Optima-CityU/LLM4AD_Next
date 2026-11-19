//! Receiver-owned queued-command origin and baseline-frozen cadence grouping.
//! Hash integrity does not attest a kernel build or turn submit cadence into display FPS.
use crate::{looptrace, pyjson};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const PROFILE: &str = "kgsl_queued_origin_spf_v1";
pub const COLLECTOR: &str = include_str!("../loop_v1/tools/render_ownership_readonly.sh");
const EVENTS: [&str; 7] = [
    "adreno_cmdbatch_queued",
    "adreno_cmdbatch_submitted",
    "kgsl_context_create",
    "kgsl_context_detach",
    "kgsl_context_destroy",
    "sched_process_fork",
    "sched_process_exit",
];
fn need(ok: bool, why: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(why.into())
    }
}
pub fn sha(b: &[u8]) -> String {
    format!("{:x}", Sha256::digest(b))
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Thread {
    pub tid: u32,
    pub start_ticks: u64,
    pub comm: String,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub profile_id: String,
    pub package: String,
    pub boot_id: String,
    pub pid: u32,
    pub process_start_ticks: u64,
    pub uid: u32,
    pub package_metadata_sha256: String,
    pub kernel_version_sha256: String,
    pub render_thread: Thread,
    pub threads: BTreeMap<u32, Thread>,
    pub device: String,
    pub event_formats_sha256: BTreeMap<String, String>,
    pub fork_parent_key: String,
    pub clock_capabilities_sha256: String,
    pub cpus: BTreeSet<u32>,
}
#[derive(Clone, Debug)]
pub struct Witness {
    pub scope: Scope,
    pub uptime_s: f64,
    pub end_uptime_s: f64,
    pub selected_clock: String,
    pub enables: BTreeMap<String, u8>,
    pub loss: BTreeMap<u32, [u64; 3]>,
    pub entries: BTreeMap<u32, usize>,
    pub filters_clear: bool,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Frozen {
    pub schema: String,
    pub scope: Scope,
    pub submits_per_group: usize,
    pub active_contexts: BTreeSet<u32>,
    pub caller_tids: BTreeSet<u32>,
    pub baselines: Vec<Baseline>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Baseline {
    pub trace_sha256: String,
    pub before_sha256: String,
    pub after_sha256: String,
    pub final_sha256: String,
    pub submits_per_group: usize,
    pub active_contexts: BTreeSet<u32>,
    pub caller_tids: BTreeSet<u32>,
    pub executor_counts: BTreeMap<u32, usize>,
    pub selected_packets: usize,
    pub boundary_submits: usize,
    pub excluded_queued_classes: usize,
}
fn blocks(raw: &str) -> Result<BTreeMap<String, String>, String> {
    need(
        raw.len() <= 2 * 1024 * 1024
            && raw.starts_with("#phonefarm_render_witness v2\n")
            && raw.ends_with("#end\n")
            && raw.lines().filter(|l| *l == "#end").count() == 1,
        "origin witness missing/truncated/too large",
    )?;
    let mut out = BTreeMap::new();
    let mut current: Option<(String, String)> = None;
    for line in raw.lines().skip(1) {
        if let Some(k) = line.strip_prefix("BEGIN\t") {
            need(
                current.is_none() && !k.is_empty() && k.len() <= 80,
                "invalid witness block",
            )?;
            current = Some((k.into(), String::new()));
        } else if let Some(k) = line.strip_prefix("END\t") {
            let (n, v) = current.take().ok_or("unexpected block end")?;
            need(
                n == k && !v.is_empty() && out.insert(n, v).is_none(),
                "duplicate/mismatched witness block",
            )?;
        } else if line == "#end" {
            need(current.is_none(), "unclosed witness block")?;
        } else {
            let (_, v) = current.as_mut().ok_or("unexpected witness record")?;
            need(v.len() + line.len() < 65536, "witness record too large")?;
            v.push_str(line);
            v.push('\n');
        }
    }
    need(
        current.is_none() && out.len() <= 850,
        "origin inventory too large",
    )?;
    Ok(out)
}
fn number(s: &str) -> Result<u32, String> {
    let n = s.parse::<u32>().map_err(|_| "invalid identity")?;
    need(n > 0 && n <= i32::MAX as u32, "invalid identity")?;
    Ok(n)
}
fn field<'a>(s: &'a str, key: &str) -> Result<&'a str, String> {
    let v = s
        .lines()
        .filter_map(|l| l.trim().strip_prefix(key))
        .collect::<Vec<_>>();
    need(v.len() == 1, "missing/duplicate raw field")?;
    Ok(v[0].trim())
}
fn epoch(stat: &str, status: &str, pid: u32, tgid: u32, uid: u32) -> Result<u64, String> {
    need(
        number(field(status, "Pid:")?)? == pid && number(field(status, "Tgid:")?)? == tgid,
        "proc PID/TGID differs",
    )?;
    let us = field(status, "Uid:")?
        .split_whitespace()
        .collect::<Vec<_>>();
    need(
        us.len() == 4 && us.iter().all(|v| v.parse::<u32>() == Ok(uid)),
        "proc UID differs",
    )?;
    let (head, tail) = stat.rsplit_once(')').ok_or("malformed proc stat")?;
    need(
        number(head.split_whitespace().next().ok_or("stat PID absent")?)? == pid,
        "stat PID differs",
    )?;
    let ticks = tail
        .split_whitespace()
        .nth(19)
        .ok_or("start ticks absent")?
        .parse::<u64>()
        .map_err(|_| "invalid start ticks")?;
    need(ticks > 0, "zero start ticks")?;
    Ok(ticks)
}
// Only a finite printf wire grammar is registered. Token presence is not a
// binding: the argument for each identity key must be the exact declared field.
fn comma_arguments(raw: &str) -> Result<Vec<&str>, String> {
    let mut quote = false;
    let mut escape = false;
    let mut nesting = Vec::new();
    let mut args = vec![];
    let mut start = 0;
    for (i, c) in raw.char_indices() {
        if quote {
            if escape {
                escape = false;
            } else if c == '\\' {
                escape = true;
            } else if c == '"' {
                quote = false;
            }
            continue;
        }
        match c {
            '"' => quote = true,
            '(' | '{' | '[' => {
                nesting.push(c);
                need(nesting.len() <= 16, "print nesting exceeds bound")?;
            }
            ')' | '}' | ']' => {
                let wanted = match c {
                    ')' => '(',
                    '}' => '{',
                    _ => '[',
                };
                need(nesting.pop() == Some(wanted), "unbalanced print arguments")?;
            }
            ',' if nesting.is_empty() => {
                args.push(raw[start..i].trim());
                start = i + 1;
                need(args.len() <= 64, "print argument count exceeds bound")?;
            }
            _ => (),
        }
    }
    need(
        !quote && !escape && nesting.is_empty(),
        "unterminated print arguments",
    )?;
    args.push(raw[start..].trim());
    need(
        args.len() <= 64 && args.iter().all(|a| !a.is_empty()),
        "empty/too many print arguments",
    )?;
    Ok(args)
}
fn print_mapping(raw: &str) -> Result<(String, Vec<&str>), String> {
    need(raw.starts_with('"'), "print literal absent")?;
    let mut escaped = false;
    let mut end = None;
    for (i, c) in raw.char_indices().skip(1) {
        if escaped {
            escaped = false;
        } else if c == '\\' {
            escaped = true;
        } else if c == '"' {
            end = Some(i);
            break;
        }
    }
    let end = end.ok_or("unterminated print literal")?;
    // The registered C literals use the common JSON escape subset only.
    let format = serde_json::from_str::<String>(&raw[..=end])
        .map_err(|_| "unregistered print literal escape")?;
    let args = comma_arguments(
        raw[end + 1..]
            .trim()
            .strip_prefix(',')
            .ok_or("print arguments absent")?,
    )?;
    Ok((format, args))
}
fn command_flag_mapping(raw: &str) -> Result<(), String> {
    let compact = raw.split_whitespace().collect::<String>();
    let table = compact
        .strip_prefix("REC->flags?__print_flags(REC->flags,\"|\",")
        .and_then(|s| s.strip_suffix("):\"none\""))
        .ok_or("command flags not bound to declared flags field")?;
    let pair = Regex::new(r#"^\{(0x[0-9a-fA-F]+),"([A-Z_]+)"\}$"#).unwrap();
    let mut parsed = BTreeMap::new();
    for entry in comma_arguments(table)? {
        let c = pair
            .captures(entry)
            .ok_or("unknown command flag expression")?;
        let bit = u32::from_str_radix(&c[1][2..], 16).map_err(|_| "flag bit invalid")?;
        need(
            parsed.insert(bit, c[2].to_owned()).is_none(),
            "duplicate command flag bit",
        )?;
    }
    let expected = [
        (2, "MARKER"),
        (0x400, "SYNC"),
        (4, "IB_LIST"),
        (8, "CTX_SWITCH"),
        (0x100, "EOF"),
        (0x800, "PWR_CONSTRAINT"),
    ]
    .into_iter()
    .map(|(b, n)| (b, n.to_owned()))
    .collect::<BTreeMap<_, _>>();
    need(
        parsed == expected,
        "command flag mapping differs from registered complete mapping",
    )
}
fn fork_parent_key(format: &str) -> Result<&str, String> {
    let has = |key: &str| {
        format
            .split_whitespace()
            .filter(|token| token.starts_with(&format!("{key}=")))
            .count()
    };
    match (has("pid"), has("parent_pid")) {
        (1, 0) => Ok("pid"),
        (0, 1) => Ok("parent_pid"),
        _ => Err("unknown/mixed/duplicate fork parent wire key".into()),
    }
}
fn event_format(raw: &str, event: &str) -> Result<(), String> {
    need(
        raw.starts_with(&format!("name: {event}\n"))
            && raw.matches("field:int common_pid;").count() == 1
            && raw.lines().filter(|l| l.starts_with("print fmt:")).count() == 1,
        "unsupported origin event format",
    )?;
    let fields: &[&str] = match event {
        "adreno_cmdbatch_queued" => &[
            "unsigned int id",
            "unsigned int timestamp",
            "unsigned int queued",
            "unsigned int flags",
            "unsigned int prio",
        ],
        "adreno_cmdbatch_submitted" => &[
            "unsigned int id",
            "unsigned int timestamp",
            "unsigned int flags",
        ],
        "kgsl_context_create" | "kgsl_context_detach" | "kgsl_context_destroy" => {
            &["unsigned int id"]
        }
        "sched_process_fork" => &["pid_t parent_pid", "pid_t child_pid"],
        "sched_process_exit" => &["pid_t pid"],
        _ => return Err("unregistered event".into()),
    };
    for f in fields {
        need(
            raw.matches(&format!("field:{f};")).count() == 1,
            "missing/duplicate typed origin field",
        )?;
    }
    let (format, args) = print_mapping(field(raw, "print fmt:")?)?;
    let conversion =
        Regex::new(r"%%|%[-+ #0]*[0-9]*(?:\.[0-9]+)?(?:hh|ll|[hljztL])?[diuoxXfFeEgGaAcsp]")
            .unwrap();
    let all = conversion.find_iter(&format).collect::<Vec<_>>();
    let used = all
        .iter()
        .flat_map(|m| m.start()..m.end())
        .collect::<BTreeSet<_>>();
    need(
        format
            .char_indices()
            .filter(|(_, c)| *c == '%')
            .all(|(i, _)| used.contains(&i)),
        "unregistered print conversion",
    )?;
    let conversions = all
        .into_iter()
        .filter(|m| m.as_str() != "%%")
        .collect::<Vec<_>>();
    need(
        conversions.len() == args.len(),
        "printf argument/conversion count mismatch",
    )?;
    let fork_key = if event == "sched_process_fork" {
        fork_parent_key(&format)?
    } else {
        "parent_pid"
    };
    let bindings: &[(&str, &str, &str)] = match event {
        "adreno_cmdbatch_queued" => &[
            ("ctx", "%u", "REC->id"),
            ("ts", "%u", "REC->timestamp"),
            ("queued", "%u", "REC->queued"),
            ("flags", "%s", ""),
        ],
        "adreno_cmdbatch_submitted" => &[
            ("ctx", "%u", "REC->id"),
            ("ts", "%u", "REC->timestamp"),
            ("flags", "%s", ""),
        ],
        "kgsl_context_create" | "kgsl_context_detach" | "kgsl_context_destroy" => &[
            ("d_name", "%s", "__get_str(device_name)"),
            ("ctx", "%u", "REC->id"),
        ],
        "sched_process_fork" => &[
            (fork_key, "%d", "REC->parent_pid"),
            ("child_pid", "%d", "REC->child_pid"),
        ],
        "sched_process_exit" => &[("pid", "%d", "REC->pid")],
        _ => unreachable!(),
    };
    for (key, spec, expression) in bindings {
        let keys = Regex::new(&format!(r"(?:^|\s){key}="))
            .unwrap()
            .find_iter(&format)
            .collect::<Vec<_>>();
        need(keys.len() == 1, "missing/duplicate printf identity key")?;
        let index = conversions
            .iter()
            .position(|m| m.start() == keys[0].end())
            .ok_or("identity printf conversion absent")?;
        need(
            conversions[index].as_str() == *spec,
            "identity printf conversion differs",
        )?;
        if *key == "flags" {
            command_flag_mapping(args[index])?;
        } else {
            need(
                args[index].split_whitespace().collect::<String>() == *expression,
                "identity printf argument binding differs",
            )?;
        }
    }
    Ok(())
}
pub fn witness(raw: &str, package: &str, comm: &str) -> Result<Witness, String> {
    let b = blocks(raw)?;
    let get = |k: &str| {
        b.get(k)
            .map(String::as_str)
            .ok_or_else(|| format!("origin witness missing {k}"))
    };
    need(get("package")?.trim() == package, "package differs")?;
    let boot = get("boot")?.trim().to_owned();
    need(!boot.is_empty() && boot.len() <= 80, "boot unknown")?;
    let uptime = |k: &str| -> Result<f64, String> {
        let n = get(k)?
            .split_whitespace()
            .next()
            .ok_or("uptime absent")?
            .parse::<f64>()
            .map_err(|_| "invalid uptime")?;
        need(n.is_finite() && n >= 0., "invalid uptime")?;
        Ok(n)
    };
    let up = uptime("uptime")?;
    let end = uptime("uptime_end")?;
    need(
        get("boot_end")?.trim() == boot && end >= up && end - up <= 20.,
        "boot/epoch capture interval changed",
    )?;
    let pid = number(get("pid")?.trim())?;
    let pkg = get("package_uid")?.split_whitespace().collect::<Vec<_>>();
    need(
        pkg.len() == 2 && pkg[0] == format!("package:{package}"),
        "package UID differs",
    )?;
    let uid = pkg[1]
        .strip_prefix("uid:")
        .ok_or("package UID missing")?
        .parse::<u32>()
        .map_err(|_| "UID invalid")?;
    let ver = get("package_version")?;
    need(
        ver.lines()
            .filter(|l| l.trim().starts_with("versionCode="))
            .count()
            == 1
            && ver
                .lines()
                .filter(|l| l.trim().starts_with("versionName="))
                .count()
                == 1,
        "version absent/ambiguous",
    )?;
    need(
        get("process_cmdline")?.lines().next() == Some(package),
        "process is not exact package",
    )?;
    let start = epoch(get("process_stat")?, get("process_status")?, pid, pid, uid)?;
    let mut threads = BTreeMap::new();
    let mut cpus = BTreeSet::new();
    let mut loss = BTreeMap::new();
    let mut entries = BTreeMap::new();
    for key in b.keys() {
        if let Some(s) = key.strip_prefix("thread_stat/") {
            let tid = number(s)?;
            let name = get(&format!("thread_comm/{tid}"))?.trim();
            need(!name.is_empty() && name.len() <= 64, "thread comm unknown")?;
            let ticks = epoch(
                get(key)?,
                get(&format!("thread_status/{tid}"))?,
                tid,
                pid,
                uid,
            )?;
            threads.insert(
                tid,
                Thread {
                    tid,
                    start_ticks: ticks,
                    comm: name.into(),
                },
            );
        }
        if let Some(s) = key.strip_prefix("stats/cpu") {
            let cpu = s.parse::<u32>().map_err(|_| "invalid CPU")?;
            need(cpu < 64, "CPU outside registered bound")?;
            let mut v = [0; 3];
            for (i, k) in ["overrun:", "commit overrun:", "dropped events:"]
                .iter()
                .enumerate()
            {
                v[i] = field(get(key)?, k)?
                    .parse()
                    .map_err(|_| "invalid trace loss statistic")?;
            }
            entries.insert(
                cpu,
                field(get(key)?, "entries:")?
                    .parse::<usize>()
                    .map_err(|_| "invalid entries count")?,
            );
            cpus.insert(cpu);
            loss.insert(cpu, v);
        }
    }
    let count = get("task_count")?
        .trim()
        .parse::<usize>()
        .map_err(|_| "task count malformed")?;
    let reported = field(get("process_status")?, "Threads:")?
        .parse::<usize>()
        .map_err(|_| "proc Threads malformed")?;
    need(
        !threads.is_empty()
            && threads.len() <= 256
            && count == threads.len()
            && reported == count
            && threads.get(&pid).is_some_and(|t| t.start_ticks == start),
        "task inventory incomplete",
    )?;
    let render = threads
        .values()
        .filter(|t| t.comm == comm)
        .collect::<Vec<_>>();
    need(
        render.len() == 1,
        "render comm absent/ambiguous (diagnostic identity)",
    )?;
    need(
        get("device")?.trim() == "kgsl-3d0" && !cpus.is_empty(),
        "single KGSL device/CPU inventory unknown",
    )?;
    let clock = get("trace_clock")?.split_whitespace().collect::<Vec<_>>();
    let selected = clock
        .iter()
        .filter(|c| c.starts_with('[') && c.ends_with(']'))
        .collect::<Vec<_>>();
    need(
        selected.len() == 1 && clock.iter().any(|c| c.trim_matches(['[', ']']) == "boot"),
        "globally comparable boot clock unavailable",
    )?;
    let selected_clock = selected[0].trim_matches(['[', ']']).into();
    let filters_clear = EVENTS.iter().all(|ev| {
        get(&format!("filter/{ev}")).is_ok_and(|v| v.trim() == "none")
            && get(&format!("trigger/{ev}"))
                .is_ok_and(|v| !v.trim().is_empty() && v.lines().filter(|l| !l.trim().is_empty()).all(|l| l.trim().starts_with('#')))
    }) && get("event_pid")?.trim() == "EMPTY"
        && get("event_notrace_pid")?.trim() == "EMPTY";
    let mask = u64::from_str_radix(&get("tracing_cpumask")?.trim().replace(',', ""), 16)
        .map_err(|_| "trace CPU mask malformed")?;
    need(
        cpus.iter().all(|cpu| mask & (1u64 << cpu) != 0),
        "trace CPU mask omits registered CPU",
    )?;
    let mut formats = BTreeMap::new();
    let mut enables = BTreeMap::new();
    for event in EVENTS {
        let r = get(&format!("format/{event}"))?;
        event_format(r, event)?;
        formats.insert(event.into(), sha(r.as_bytes()));
        let enabled = get(&format!("enabled/{event}"))?
            .trim()
            .parse::<u8>()
            .map_err(|_| "event enable malformed")?;
        need(enabled <= 1, "event enable unknown")?;
        enables.insert(event.into(), enabled);
    }
    let mut allowed = [
        "package",
        "boot",
        "uptime",
        "kernel",
        "pid",
        "package_uid",
        "package_version",
        "process_cmdline",
        "process_stat",
        "process_status",
        "task_count",
        "device",
        "trace_clock",
        "event_pid",
        "event_notrace_pid",
        "tracing_cpumask",
        "boot_end",
        "uptime_end",
    ]
    .into_iter()
    .map(String::from)
    .collect::<BTreeSet<_>>();
    for tid in threads.keys() {
        for kind in ["thread_stat", "thread_status", "thread_comm"] {
            allowed.insert(format!("{kind}/{tid}"));
        }
    }
    for event in EVENTS {
        allowed.insert(format!("format/{event}"));
        allowed.insert(format!("enabled/{event}"));
        allowed.insert(format!("filter/{event}"));
        allowed.insert(format!("trigger/{event}"));
    }
    for cpu in &cpus {
        allowed.insert(format!("stats/cpu{cpu}"));
    }
    need(
        allowed == b.keys().cloned().collect(),
        "unknown/incomplete witness inventory",
    )?;
    Ok(Witness {
        scope: Scope {
            profile_id: PROFILE.into(),
            package: package.into(),
            boot_id: boot,
            pid,
            process_start_ticks: start,
            uid,
            package_metadata_sha256: sha(format!("{}{ver}", get("package_uid")?).as_bytes()),
            kernel_version_sha256: sha(get("kernel")?.as_bytes()),
            render_thread: render[0].clone(),
            threads: threads.clone(),
            device: "kgsl-3d0".into(),
            event_formats_sha256: formats,
            fork_parent_key: fork_parent_key(
                &print_mapping(field(get("format/sched_process_fork")?, "print fmt:")?)?.0,
            )?
            .into(),
            clock_capabilities_sha256: sha(clock
                .iter()
                .map(|c| c.trim_matches(['[', ']']))
                .collect::<Vec<_>>()
                .join(" ")
                .as_bytes()),
            cpus,
        },
        uptime_s: up,
        end_uptime_s: end,
        selected_clock,
        enables,
        loss,
        entries,
        filters_clear,
    })
}
#[derive(Clone)]
struct Event<'a> {
    tid: u32,
    wall: f64,
    event: &'a str,
    rest: &'a str,
    line: &'a str,
    order: usize,
}
fn kv<'a>(s: &'a str, k: &str) -> Result<&'a str, String> {
    let prefix = format!("{k}=");
    let vals = s
        .split_whitespace()
        .filter_map(|t| t.strip_prefix(&prefix))
        .collect::<Vec<_>>();
    need(
        vals.len() == 1 && !vals[0].is_empty(),
        "missing/duplicate event identity",
    )?;
    Ok(vals[0])
}
fn u32kv(s: &str, k: &str) -> Result<u32, String> {
    kv(s, k)?.parse().map_err(|_| format!("malformed {k}"))
}
fn key(e: &Event<'_>) -> Result<(u32, u32), String> {
    let ctx = u32kv(e.rest, "ctx")?;
    need(ctx > 0, "zero context")?;
    Ok((ctx, u32kv(e.rest, "ts")?))
}
fn flags(s: &str) -> Result<bool, String> {
    let value = kv(s, "flags")?;
    if value == "none" {
        return Ok(true);
    }
    let parts = value.split('|').collect::<Vec<_>>();
    need(
        parts.iter().collect::<BTreeSet<_>>().len() == parts.len()
            && parts.iter().all(|v| {
                matches!(
                    *v,
                    "MARKER" | "SYNC" | "CTX_SWITCH" | "EOF" | "PWR_CONSTRAINT" | "IB_LIST"
                )
            }),
        "unknown/duplicate drawobj flag",
    )?;
    Ok(!parts.iter().any(|v| matches!(*v, "MARKER" | "SYNC")))
}
pub fn analyze(
    trace: &str,
    before: &str,
    after: &str,
    final_raw: &str,
    scope: &Scope,
    frozen: Option<&Frozen>,
) -> Result<(Value, Baseline), String> {
    need(
        trace.len() <= 128 * 1024 * 1024,
        "trace exceeds receiver bound",
    )?;
    let a = witness(before, &scope.package, &scope.render_thread.comm)?;
    let b = witness(after, &scope.package, &scope.render_thread.comm)?;
    let c = witness(final_raw, &scope.package, &scope.render_thread.comm)?;
    need(
        a.scope == *scope && b.scope == *scope && c.scope == *scope,
        "origin package/proc/task/kernel/format epoch changed",
    )?;
    need(
        a.filters_clear
            && b.filters_clear
            && c.filters_clear
            && a.selected_clock == "boot"
            && b.selected_clock == "boot"
            && c.selected_clock == "boot"
            && a.enables
                .values()
                .chain(b.enables.values())
                .chain(c.enables.values())
                .all(|v| *v == 1)
            && a.loss
                .values()
                .chain(b.loss.values())
                .chain(c.loss.values())
                .all(|v| *v == [0; 3]),
        "clock alignment/trace loss unknown",
    )?;
    let bounds = crate::confirmation_observation::trace_bounds(trace)
        .ok_or("measurement trace bounds absent")?;
    let meta = trace
        .lines()
        .filter(|l| l.starts_with("#loop_v1_meta ") && l.contains("capture_start_uptime="))
        .collect::<Vec<_>>();
    need(meta.len() == 1, "duplicate/missing trace meta")?;
    let num = |k: &str| -> Result<f64, String> {
        let v = kv(meta[0], k)?
            .parse::<f64>()
            .map_err(|_| "invalid join bounds")?;
        need(v.is_finite(), "invalid join bounds")?;
        Ok(v)
    };
    let join = (num("join_start_uptime")?, num("join_end_uptime")?);
    let tail_end = num("tail_end_uptime")?;
    need(
        join.0 <= a.uptime_s
            && a.end_uptime_s < bounds.0
            && bounds.0 < bounds.1
            && bounds.1 < tail_end
            && tail_end <= b.uptime_s
            && b.end_uptime_s <= join.1
            && join.1 <= c.uptime_s
            && join.1 - join.0 <= 1200.
            && tail_end - bounds.1 >= 2.9
            && tail_end - bounds.1 <= 3.5
            && a.uptime_s - join.0 <= 20.
            && bounds.0 - a.end_uptime_s <= 40.
            && b.uptime_s - tail_end <= 20.
            && join.1 - b.end_uptime_s <= 20.
            && c.uptime_s - join.1 <= 20.,
        "raw witness/continuous lifecycle/capture/tail/final bounds inconsistent",
    )?;
    let re = Regex::new(r"^\s*.+?-(\d+)\s+\[(\d+)\]\s+\S+\s+(\d+\.\d+):\s+(\w+):\s*(.*)$").unwrap();
    let mut actual_entries = BTreeMap::<u32, usize>::new();
    let mut total_entries = 0usize;
    let mut events = Vec::new();
    let mut last = join.0;
    for (order, line) in trace.lines().enumerate() {
        if line.starts_with('#') || line.trim().is_empty() {
            continue;
        }
        let c = re
            .captures(line)
            .ok_or("malformed required origin/lifecycle trace line")?;
        let event = c.get(4).unwrap().as_str();
        need(
            EVENTS.contains(&event),
            "unexpected trace event in registered capture",
        )?;
        let tid = number(&c[1])?;
        let cpu = c[2].parse::<u32>().map_err(|_| "CPU malformed")?;
        let wall = c[3].parse::<f64>().map_err(|_| "time malformed")?;
        need(
            scope.cpus.contains(&cpu)
                && wall.is_finite()
                && wall >= last
                && wall >= join.0
                && wall <= join.1,
            "event CPU/clock order/span unknown",
        )?;
        last = wall;
        total_entries += 1;
        need(
            total_entries <= 250000,
            "total trace entries exceed receiver bound",
        )?;
        *actual_entries.entry(cpu).or_insert(0) += 1;
        events.push(Event {
            tid,
            wall,
            event,
            rest: c.get(5).unwrap().as_str(),
            line,
            order,
        });
        need(events.len() <= 250000, "origin events exceed bound")?;
    }
    need(
        scope
            .cpus
            .iter()
            .all(|cpu| actual_entries.get(cpu).copied().unwrap_or(0) == c.entries[cpu]),
        "raw trace entries do not match complete per-CPU buffer inventory",
    )?;
    let mut queues = BTreeMap::new();
    let mut submits = BTreeMap::new();
    let mut lifecycle = BTreeSet::new();
    let mut sequence = BTreeMap::new();
    let mut excluded = 0;
    for e in &events {
        match e.event {
            "sched_process_fork" => {
                let other = if scope.fork_parent_key == "pid" {
                    "parent_pid"
                } else {
                    "pid"
                };
                need(
                    !e.rest
                        .split_whitespace()
                        .any(|t| t.starts_with(&format!("{other}="))),
                    "mixed fork parent payload key",
                )?;
                let parent = u32kv(e.rest, &scope.fork_parent_key)?;
                let child = u32kv(e.rest, "child_pid")?;
                need(
                    !scope.threads.contains_key(&parent) && !scope.threads.contains_key(&child),
                    "target task fork/reuse during capture",
                )?;
            }
            "sched_process_exit" => {
                let pid = u32kv(e.rest, "pid")?;
                need(
                    !scope.threads.contains_key(&pid),
                    "target task exit during capture",
                )?;
            }
            "kgsl_context_create" | "kgsl_context_detach" | "kgsl_context_destroy" => {
                need(
                    kv(e.rest, "d_name")? == scope.device,
                    "lifecycle device differs",
                )?;
                lifecycle.insert(u32kv(e.rest, "ctx")?);
            }
            "adreno_cmdbatch_queued" => {
                let k = key(e)?;
                let command = flags(e.rest)?;
                u32kv(e.rest, "queued")?;
                if !command {
                    excluded += 1;
                    continue;
                }
                need(
                    k.1 > 0 && queues.insert(k, e).is_none(),
                    "zero/duplicate queued command key",
                )?;
                if let Some(prev) = sequence.insert(k.0, k.1) {
                    need(k.1 > prev, "context sequence wrap/reorder")?;
                }
            }
            "adreno_cmdbatch_submitted" => {
                let k = key(e)?;
                need(
                    k.1 > 0 && flags(e.rest)? && submits.insert(k, e).is_none(),
                    "zero/unsupported/replayed submitted command",
                )?;
            }
            _ => unreachable!(),
        }
    }
    let observed = queues
        .keys()
        .chain(submits.keys())
        .map(|k| k.0)
        .collect::<BTreeSet<_>>();
    need(
        lifecycle.is_disjoint(&observed),
        "context generation changed during join span",
    )?;
    let inside = |e: &Event<'_>| e.wall >= bounds.0 && e.wall <= bounds.1;
    let mut selected = BTreeSet::new();
    let mut callers = BTreeSet::new();
    let mut contexts = BTreeSet::new();
    let mut executor_counts = BTreeMap::new();
    let mut boundary = 0;
    for (k, s) in &submits {
        if !inside(s) {
            boundary += 1;
            continue;
        }
        let q = queues
            .get(k)
            .ok_or("measurement submitted packet has no queued origin")?;
        need(
            q.order < s.order && q.wall <= s.wall,
            "queued origin does not precede submitted",
        )?;
        need(
            q.wall >= a.end_uptime_s + 0.01,
            "measurement queued origin precedes complete task witness; origin UNKNOWN",
        )?;
        if scope.threads.contains_key(&q.tid) {
            selected.insert(*k);
            callers.insert(q.tid);
            contexts.insert(k.0);
            *executor_counts.entry(s.tid).or_insert(0) += 1;
        }
    }
    for (k, q) in &queues {
        if inside(q) && scope.threads.contains_key(&q.tid) {
            // Freeze every measurement-origin caller/context, including commands
            // submitted only in the fixed tail. Tail packets stay outside metrics.
            callers.insert(q.tid);
            contexts.insert(k.0);
            let s = submits
                .get(k)
                .ok_or("target queued command unresolved at frozen tail")?;
            need(
                q.order < s.order && q.wall <= s.wall && s.wall <= tail_end,
                "target measurement queued command unresolved at frozen tail",
            )?;
        }
    }
    need(
        selected.len() >= 12,
        "target-origin submitted cadence absent/insufficient",
    )?;
    // Parse only the exact joined measurement keys, never a comm or context subset heuristic.
    let mut filtered = String::from(meta[0]);
    filtered.push('\n');
    for e in &events {
        if e.event == "adreno_cmdbatch_submitted" && selected.contains(&key(e)?) {
            filtered.push_str(e.line);
            filtered.push('\n');
        }
    }
    let owned = contexts.iter().map(|c| i64::from(*c)).collect();
    let inferred =
        looptrace::parse_owned_contexts(&filtered, &scope.render_thread.comm, &owned, None);
    let spf = inferred.submits_per_frame;
    need(
        (1..=6).contains(&spf)
            && inferred
                .spf_scores
                .acf
                .iter()
                .any(|(n, v)| *n == spf && v.is_finite() && *v >= 0.30),
        "grouping inference unknown",
    )?;
    need(
        inferred
            .spf_scores
            .acf
            .iter()
            .filter(|(_, v)| *v >= 0.30)
            .all(|(n, _)| n % spf == 0),
        "grouping inference ambiguous",
    )?;
    if let Some(f) = frozen {
        need(
            f.schema == "phonefarm/frozen_render_attribution@1"
                && f.scope == *scope
                && f.submits_per_group == spf
                && f.active_contexts == contexts
                && f.caller_tids == callers,
            "heldout grouping/caller/context scope differs from frozen baseline",
        )?;
    }
    let measured = looptrace::parse_owned_contexts(
        &filtered,
        &scope.render_thread.comm,
        &owned,
        Some(frozen.map_or(spf, |f| f.submits_per_group)),
    );
    let summary =
        serde_json::from_str(&pyjson::dumps(&measured.summarize())).map_err(|e| e.to_string())?;
    Ok((
        summary,
        Baseline {
            trace_sha256: sha(trace.as_bytes()),
            before_sha256: sha(before.as_bytes()),
            after_sha256: sha(after.as_bytes()),
            final_sha256: sha(final_raw.as_bytes()),
            submits_per_group: spf,
            active_contexts: contexts,
            caller_tids: callers,
            executor_counts,
            selected_packets: selected.len(),
            boundary_submits: boundary,
            excluded_queued_classes: excluded,
        },
    ))
}
pub fn freeze(scope: &Scope, baselines: Vec<Baseline>) -> Result<Frozen, String> {
    need(
        baselines.len() == 3,
        "exactly three origin admission baselines required",
    )?;
    let first = &baselines[0];
    need(
        baselines.iter().all(|b| {
            b.submits_per_group == first.submits_per_group
                && b.active_contexts == first.active_contexts
                && b.caller_tids == first.caller_tids
        }),
        "baseline grouping/caller/context scope unstable",
    )?;
    need(
        baselines
            .iter()
            .flat_map(|b| {
                [
                    &b.trace_sha256,
                    &b.before_sha256,
                    &b.after_sha256,
                    &b.final_sha256,
                ]
            })
            .collect::<BTreeSet<_>>()
            .len()
            == 12,
        "baseline origin evidence reused",
    )?;
    Ok(Frozen {
        schema: "phonefarm/frozen_render_attribution@1".into(),
        scope: scope.clone(),
        submits_per_group: first.submits_per_group,
        active_contexts: first.active_contexts.clone(),
        caller_tids: first.caller_tids.clone(),
        baselines,
    })
}
pub fn diagnostic(scope: &Scope, b: &Baseline) -> Value {
    json!({"profile_id":PROFILE,"origin_scope":"original target-TGID queued commands uniquely joined to measurement submitted packets; all executors; per-capture opaque context generation","scope":scope,"record":b,"display_verified":false,"context_process_ownership_verified":false,"installed_kernel_source_verified":false,"continuous_foreground_verified":false,"quality_verified":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_reader_actual_host_countercases() {
        let test = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../tools/render-collector/test_reader.py");
        let result = std::process::Command::new("python3")
            .arg(test)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        assert!(String::from_utf8_lossy(&result.stderr).contains("Ran 7 tests"));
    }
    fn raw(up: f64) -> String {
        include_str!("tests/support/render_origin.synthetic.raw.tsv")
            .replace("UPTIME_END_PLACEHOLDER", &(up + 0.1).to_string())
            .replace("UPTIME_PLACEHOLDER", &up.to_string())
            .replace("[local] boot global", "local [boot] global")
    }
    fn trace(spf: usize, base: f64) -> String {
        let mut s=format!("#loop_v1_meta capture_start_uptime={base} capture_end_uptime={} duration_req=3 join_start_uptime={} tail_end_uptime={} join_end_uptime={}\n",base+3.,base-1.3,base+6.,base+6.3);
        let pattern = match spf {
            1 => vec![15., 15., 16., 17., 18., 18., 17., 16.],
            2 => vec![3., 30.],
            3 => vec![2., 5., 26.],
            _ => unreachable!(),
        };
        let mut t = base + 0.1;
        for i in 0..150 {
            let ctx = if i % 2 == 0 { 41 } else { 42 };
            let tid = if i % 2 == 0 { 32074 } else { 32075 };
            s+=&format!(" game-{tid} [000] ..... {:.6}: adreno_cmdbatch_queued: ctx={ctx} ctx_prio=0 ts={} queued=2 flags=IB_LIST\n worker-555 [003] ..... {t:.6}: adreno_cmdbatch_submitted: ctx={ctx} ts={} flags=IB_LIST\n",t-0.0005,i+1,i+1);
            t += pattern[i % pattern.len()] / 1000.;
        }
        s
    }
    fn with_entries(mut raw: String, t: &str) -> String {
        for cpu in [0, 3] {
            let n = t
                .lines()
                .filter(|l| l.contains(&format!("[{cpu:03}]")))
                .count();
            let key = format!("BEGIN\tstats/cpu{cpu}\nentries: 0");
            raw = raw.replace(&key, &format!("BEGIN\tstats/cpu{cpu}\nentries: {n}"));
        }
        raw
    }
    fn inspect(t: &str, base: f64, f: Option<&Frozen>) -> Result<(Value, Baseline), String> {
        let a = raw(base - 1.2);
        let b = raw(base + 6.1);
        let c = with_entries(raw(base + 6.4), t);
        let scope = witness(&a, "com.example.game", "UnityGfxDeviceW")?.scope;
        analyze(t, &a, &b, &c, &scope, f)
    }
    fn frozen() -> Frozen {
        let rows = (0..3)
            .map(|i| {
                inspect(
                    &trace(2, 100. + i as f64 * 20.),
                    100. + i as f64 * 20.,
                    None,
                )
                .unwrap()
                .1
            })
            .collect();
        freeze(
            &witness(&raw(98.8), "com.example.game", "UnityGfxDeviceW")
                .unwrap()
                .scope,
            rows,
        )
        .unwrap()
    }
    #[test]
    fn actual_oneplus_format_records_and_finite_fork_pid_alias_are_supported() {
        let raw = include_str!("tests/support/kgsl-formats-oneplus20261012.tsv");
        for part in raw.split("BEGIN\t").skip(1) {
            let (event, body) = part.split_once('\n').unwrap();
            let body = body.split(&format!("END\t{event}\n")).next().unwrap();
            event_format(body, event).unwrap();
        }
        // The installed format labels original parent_pid as pid=; freeze that
        // exact key and reject payload/format mixtures or swapped expressions.
        let witness_raw = self::raw(98.8).replace("comm=%s parent_pid=%d", "comm=%s pid=%d");
        let scope = witness(&witness_raw, "com.example.game", "UnityGfxDeviceW")
            .unwrap()
            .scope;
        assert_eq!(scope.fork_parent_key, "pid");
        let mut lines = trace(2, 100.).lines().map(String::from).collect::<Vec<_>>();
        lines.insert(2, " lifecycle-32074 [000] ..... 100.099800: sched_process_fork: comm=game pid=32074 child_comm=child child_pid=777".into());
        let t = lines.join("\n") + "\n";
        let post = self::raw(106.1).replace("comm=%s parent_pid=%d", "comm=%s pid=%d");
        let final_raw = with_entries(
            self::raw(106.4).replace("comm=%s parent_pid=%d", "comm=%s pid=%d"),
            &t,
        );
        let result = analyze(&t, &witness_raw, &post, &final_raw, &scope, None);
        let error = result.unwrap_err();
        assert!(error.contains("fork/reuse"), "{error}");
        assert!(witness(
            &witness_raw.replace("pid=%d child_comm", "pid=%d parent_pid=%d child_comm"),
            "com.example.game",
            "UnityGfxDeviceW"
        )
        .is_err());
        assert!(witness(
            &witness_raw.replace(
                "REC->parent_pid, REC->child_comm, REC->child_pid",
                "REC->child_pid, REC->child_comm, REC->parent_pid"
            ),
            "com.example.game",
            "UnityGfxDeviceW"
        )
        .is_err());
    }
    #[test]
    fn empty_trigger_lines_are_not_active_trigger_records() {
        let r=raw(98.8).replace("# Available triggers:\n", "# Available triggers:\n\n");
        assert!(witness(&r,"com.example.game","UnityGfxDeviceW").unwrap().filters_clear);
        let bad=r.replace("# Available triggers:\n", "# Available triggers:\ntraceoff:unlimited\n");
        assert!(!witness(&bad,"com.example.game","UnityGfxDeviceW").unwrap().filters_clear);
        let empty=r.replace("# Available triggers:\n", "").replace("# traceon traceoff\n", "");
        assert!(!witness(&empty,"com.example.game","UnityGfxDeviceW").unwrap().filters_clear);
    }
    #[test]
    fn original_callers_multi_context_worker_executors_are_retained() {
        let (_, b) = inspect(&trace(2, 100.), 100., None).unwrap();
        assert_eq!(b.selected_packets, 150);
        assert_eq!(b.active_contexts, BTreeSet::from([41, 42]));
        assert_eq!(b.caller_tids, BTreeSet::from([32074, 32075]));
        assert_eq!(b.executor_counts, BTreeMap::from([(555, 150)]));
    }
    #[test]
    fn same_name_foreign_origin_cannot_supply_target_metrics() {
        let t = trace(2, 100.)
            .replace("game-32074", "UnityGfxDeviceW-2222")
            .replace("game-32075", "UnityGfxDeviceW-2223");
        assert!(inspect(&t, 100., None)
            .unwrap_err()
            .contains("absent/insufficient"));
    }
    #[test]
    fn duplicate_unmatched_replay_and_unknown_flags_reject() {
        let t = trace(2, 100.);
        for t in [
            t.replace("ts=1 ", "ts=3 "),
            t.replace("adreno_cmdbatch_queued:", "unknown_event:"),
            t.replace("flags=IB_LIST", "flags=UNKNOWN"),
            t.replace("ts=1 ", "ts=0 "),
            t.replacen("ctx=41 ctx_prio=0 ts=1", "ctx=41 ctx=42 ctx_prio=0 ts=1", 1),
        ] {
            assert!(inspect(&t, 100., None).is_err());
        }
    }
    #[test]
    fn loss_clock_enable_missing_format_and_epoch_reject() {
        let t = trace(2, 100.);
        let a = raw(98.8);
        let b = raw(106.4);
        let scope = witness(&a, "com.example.game", "UnityGfxDeviceW")
            .unwrap()
            .scope;
        for bad in [
            b.replace("overrun: 0", "overrun: 1"),
            b.replace("local [boot] global", "[local] boot global"),
            b.replace("32000 (fixture)", "32001 (fixture)"),
            b.replace("END\tformat/adreno_cmdbatch_queued", "END\twrong"),
            b.replace(
                "BEGIN\tenabled/adreno_cmdbatch_queued\n1",
                "BEGIN\tenabled/adreno_cmdbatch_queued\n0",
            ),
            b.replace("320740", "320741"),
        ] {
            assert!(analyze(&t, &a, &raw(106.1), &bad, &scope, None).is_err());
        }
    }
    #[test]
    fn task_or_context_lifecycle_changes_reject() {
        let t = trace(2, 100.);
        for rest in [
            "sched_process_exit: comm=game pid=32074 prio=0",
            "sched_process_fork: comm=game parent_pid=32075 child_comm=game child_pid=999",
            "kgsl_context_destroy: d_name=kgsl-3d0 ctx=41",
        ] {
            let mut lines = t.lines().map(String::from).collect::<Vec<_>>();
            lines.insert(2, format!(" lifecycle-888 [000] ..... 100.099800: {rest}"));
            assert!(inspect(&(lines.join("\n") + "\n"), 100., None).is_err());
        }
    }
    #[test]
    fn baseline_frozen_spf_is_used_and_changes_reject() {
        let f = frozen();
        assert_eq!(f.submits_per_group, 2);
        assert!(inspect(&trace(2, 170.), 170., Some(&f)).is_ok());
        for n in [1, 3] {
            assert!(inspect(&trace(n, 170.), 170., Some(&f)).is_err());
        }
        let mut edited = f.clone();
        edited.submits_per_group = 3;
        assert!(inspect(&trace(2, 170.), 170., Some(&edited)).is_err());
    }
    #[test]
    fn measurement_origin_tail_submit_freezes_scope_without_counting_metric() {
        let f = frozen();
        let mut t = trace(2, 100.);
        t += " game-32000 [000] ..... 102.990000: adreno_cmdbatch_queued: ctx=99 ctx_prio=0 ts=1 queued=1 flags=IB_LIST\n worker-555 [003] ..... 103.100000: adreno_cmdbatch_submitted: ctx=99 ts=1 flags=IB_LIST\n";
        let (_, observed) = inspect(&t, 100., None).unwrap();
        assert_eq!(observed.selected_packets, f.baselines[0].selected_packets);
        assert!(observed.active_contexts.contains(&99));
        assert!(observed.caller_tids.contains(&32000));
        assert_eq!(observed.boundary_submits, 1);
        // Post-witness events remain in raw completeness, but cannot resolve
        // a measurement command after the registered 3s join tail.
        let late = t.replace("103.100000", "106.150000");
        assert!(inspect(&late, 100., None)
            .unwrap_err()
            .contains("unresolved at frozen tail"));
        assert!(inspect(&t, 100., Some(&f))
            .unwrap_err()
            .contains("scope differs"));
    }
    #[test]
    fn lifecycle_is_continuous_across_witnesses_and_early_origins_are_unknown() {
        let t = trace(2, 100.);
        // A complete endpoint table alone misses a thread created and exited
        // in the witness/setup prefix. The continuous lifecycle now rejects it.
        let mut lines = t.lines().map(String::from).collect::<Vec<_>>();
        lines.insert(1, " lifecycle-32074 [000] ..... 98.850000: sched_process_fork: parent_pid=32074 child_pid=777".into());
        lines.insert(
            2,
            " lifecycle-777 [000] ..... 98.900000: sched_process_exit: pid=777".into(),
        );
        assert!(inspect(&(lines.join("\n") + "\n"), 100., None)
            .unwrap_err()
            .contains("fork/reuse"));
        for bad in [
            t.replace("join_start_uptime=98.7", "join_start_uptime=99"),
            t.replace("join_end_uptime=106.3", "join_end_uptime=106.05"),
        ] {
            assert!(inspect(&bad, 100., None)
                .unwrap_err()
                .contains("continuous lifecycle"));
        }
        let early = t.replacen(
            "100.099500: adreno_cmdbatch_queued",
            "98.850000: adreno_cmdbatch_queued",
            1,
        );
        for bad in [
            early.clone(),
            early.replacen("game-32074", "foreign-2222", 1),
        ] {
            assert!(inspect(&bad, 100., None)
                .unwrap_err()
                .contains("precedes complete task witness"));
        }
    }
    #[test]
    fn print_wire_bindings_and_complete_unique_flags_are_required() {
        let valid = raw(98.8);
        for bad in [
            valid.replace(
                "REC->id, REC->prio, REC->timestamp",
                "REC->timestamp, REC->prio, REC->id",
            ),
            valid.replace(
                "REC->id, REC->timestamp, REC->flags",
                "REC->timestamp, REC->id, REC->flags",
            ),
            valid.replace(
                "REC->parent_pid, REC->child_comm, REC->child_pid",
                "REC->child_pid, REC->child_comm, REC->parent_pid",
            ),
            valid.replace(
                "{ 0x00000002, \"MARKER\" }",
                "{ 0x00000002, \"MARKER\" }, { 0x00000002, \"MARKER\" }",
            ),
            valid.replace("{ 0x00000004, \"IB_LIST\" }", "{ 0x00000004, \"SYNC\" }"),
            valid.replace(
                "__get_str(device_name), REC->id",
                "REC->id, __get_str(device_name)",
            ),
        ] {
            assert!(witness(&bad, "com.example.game", "UnityGfxDeviceW").is_err());
        }
        // Realistic diagnostic fields before and after flags do not change
        // the finite positional ctx/ts/flags bindings. No device attestation.
        let richer = valid.replace("\"ctx=%u ts=%u flags=%s\", REC->id, REC->timestamp", "\"ctx=%u ctx_prio=%d ts=%u inflight=%d flags=%s ticks=%lld time=%lu.%0lu rb_id=%d r/w=%x/%x, q_inflight=%d dq_id=%d\", REC->id, REC->prio, REC->timestamp, REC->inflight")
;
        // Bind all added conversions explicitly at the end of this print line.
        let mut lines = richer.lines().map(String::from).collect::<Vec<_>>();
        let print = lines
            .iter_mut()
            .find(|l| l.starts_with("print fmt: \"ctx=%u ctx_prio=%d"))
            .unwrap();
        print.push_str(", REC->ticks, REC->secs, REC->usecs, REC->rb_id, REC->rptr, REC->wptr, REC->q_inflight, REC->dispatch_queue");
        assert!(witness(
            &(lines.join("\n") + "\n"),
            "com.example.game",
            "UnityGfxDeviceW"
        )
        .is_ok());
    }
    #[test]
    fn incomplete_task_inventory_or_reused_baselines_reject() {
        let a = raw(98.8);
        assert!(witness(
            &a.replace("Threads:\t3", "Threads:\t4"),
            "com.example.game",
            "UnityGfxDeviceW"
        )
        .is_err());
        let f = frozen();
        assert!(freeze(&f.scope, vec![f.baselines[0].clone(); 3]).is_err());
    }
    #[test]
    fn exact_entries_filters_and_cpu_mask_are_receiver_gates() {
        let t = trace(2, 100.);
        let a = raw(98.8);
        let b = with_entries(raw(106.4), &t);
        let scope = witness(&a, "com.example.game", "UnityGfxDeviceW")
            .unwrap()
            .scope;
        for bad in [
            b.replace("entries: 150", "entries: 149"),
            b.replace(
                "BEGIN\tfilter/adreno_cmdbatch_queued\nnone",
                "BEGIN\tfilter/adreno_cmdbatch_queued\npid == 32074",
            ),
            b.replace(
                "BEGIN\ttrigger/adreno_cmdbatch_queued\n# Available triggers:",
                "BEGIN\ttrigger/adreno_cmdbatch_queued\ntraceoff:unlimited",
            ),
            b.replace("BEGIN\tevent_pid\nEMPTY", "BEGIN\tevent_pid\nEMPTY32074"),
            b.replace("BEGIN\ttracing_cpumask\nf", "BEGIN\ttracing_cpumask\n1"),
        ] {
            assert!(analyze(&t, &a, &raw(106.1), &bad, &scope, None).is_err());
        }
        let mut truncated = t.lines().map(String::from).collect::<Vec<_>>();
        truncated.remove(3);
        assert!(analyze(
            &(truncated.join("\n") + "\n"),
            &a,
            &raw(106.1),
            &b,
            &scope,
            None
        )
        .is_err());
    }
    #[test]
    fn unresolved_measurement_target_queue_is_unknown() {
        let mut t = trace(2, 100.);
        t+= " game-32074 [000] ..... 102.990000: adreno_cmdbatch_queued: ctx=41 ctx_prio=0 ts=999 queued=2 flags=IB_LIST\n";
        assert!(inspect(&t, 100., None).unwrap_err().contains("unresolved"));
    }
}
