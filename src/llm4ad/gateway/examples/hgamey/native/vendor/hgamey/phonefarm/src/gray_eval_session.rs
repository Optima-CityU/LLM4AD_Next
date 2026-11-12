//! Owned resources for gray eval; one base session owns the actual lease.
use crate::{confirmation_transport::{new_file, quote as q}, gray_session::Session};
use serde_json::{json, Value};
use std::{cell::RefCell, path::Path, time::{Duration, Instant}};

const SCHEMA: &str = "phonefarm/gray-eval-launch-session@1";
const SCRIPT_SCHEMA: &str = "phonefarm/gray-eval-script-session@1";
const LOADOP_SCHEMA: &str = "phonefarm/gray-ab-loadop-session@1";
const CONTROL_SCHEMA: &str = crate::gray_session::CONTROL_PARENT_SCHEMA;
const INPUT_CONTROL_SCHEMA: &str = crate::gray_session::INPUT_CONTROL_PARENT_SCHEMA;
const LEGACY_CONTROL_SCHEMA: &str = crate::gray_session::LEGACY_CONTROL_PARENT_SCHEMA;
const CHILD_GUARD: &str = include_str!("../loop_v1/tools/gray_child_guard.sh");
const FAN: &str = "/sys/kernel/fan/fan_enable";
const WORKER: &str = include_str!("../loop_v1/tools/confirmation_worker.sh");
const BOOTSTRAP: &str = include_str!("../loop_v1/tools/confirmation_bootstrap.sh");
const TRACE: &str = include_str!("../loop_v1/tools/gray_trace_workload.sh");
const SAMPLER: &str = include_str!("../loop_v1/auto/sample_env.sh");

/// Host-only admission; the same check is repeated under the actual lease.
pub(crate) fn admit_control_power(serial:&str,policy:&str)->Result<(),String> {
    match policy {
        "observe"=>Ok(()),
        "battery"=>{
            let path=crate::hwcond::lock_state_path("charge",&Some(serial.into()))?;
            if crate::hwcond::charge_record(&path)?.is_some() {return Err("existing charging record is not owned by this new control session".into());}
            Ok(())
        },
        _=>Err("power policy must be observe or battery".into()),
    }
}

pub(crate) struct Parent {
    pub base: Session,
    state: RefCell<Value>,
    charge: RefCell<Option<crate::hwcond::ChargeSuspend>>,
}

impl Parent {
    pub fn new(base: Session, request: &Value) -> Result<Self, String> {
        Self::new_with_plan(base, request, None)
    }
    pub fn new_script(base: Session, request: &Value, plan: &crate::gray_script::Plan) -> Result<Self,String> {
        Self::new_with_plan(base, request, Some(plan))
    }
    pub fn new_loadop(base:Session,request:&Value,plan:&crate::gray_script::Plan)->Result<Self,String> {
        if base.eval_identity()?["completion_owner"]!="gray_ab_loadop" {return Err("loadop parent owner differs".into());}
        Self::new_with_plan(base,request,Some(plan))
    }
    pub fn new_control(base:Session,power_policy:&str)->Result<Self,String> {
        if base.control_identity()?["control_parent_schema"]!=CONTROL_SCHEMA {return Err("new control parent requires the current resource binding".into());}
        if !matches!(power_policy,"observe"|"battery") {return Err("power policy must be observe or battery".into());}
        Self::new_with_plan(base,&json!({"protocol":"current_scene","power_policy":power_policy}),None)
    }
    pub fn resume_control(base:Session)->Result<Self,String> {
        if !base.control_has_resources()? {return Err("legacy control session has no resource parent".into());}
        Self::resume_base(base)
    }
    fn new_with_plan(base: Session, request: &Value, plan: Option<&crate::gray_script::Plan>) -> Result<Self,String> {
        let id = base.eval_identity()?;
        let loadop=id["completion_owner"]=="gray_ab_loadop";
        let control=id["completion_owner"]=="gray_layer_control";
        if (loadop || id["completion_owner"]=="gray_eval_script") != plan.is_some() {return Err("parent plan/owner mismatch".into());}
        // Foreign/legacy/partial charging records are refused before any device write.
        let charging=if loadop || (control && request["power_policy"]=="battery") {
            let path=crate::hwcond::lock_state_path("charge",&Some(id["serial"].as_str().ok_or("missing serial")?.into()))?;
            if crate::hwcond::charge_record(&path)?.is_some() {return Err(format!("charging record {} is not owned by this new session",path.display()));}
            json!({"stage":"not_started","record_path":path,"binding":null,"history":[],"initial":null,"last_observation":null})
        }else{Value::Null};
        let token = id["token"].as_str().ok_or("missing token")?;
        let state = json!({"schema":if control{CONTROL_SCHEMA}else if loadop{LOADOP_SCHEMA}else if plan.is_some(){SCRIPT_SCHEMA}else{SCHEMA},"identity":id,"stage":"created","request":request,"input_plan":plan.map(|p|p.to_value()),
            "remote":format!("/data/local/tmp/phonefarm-confirmation-gray-{token}"),
            "trace":format!("/sys/kernel/tracing/instances/phonefarm_gray_{token}"),
            "deployment":"not_started","tools_created":false,"trace_state":"not_started","fan":null,"pending":null,"rounds":[],"charging":charging,"power_policy":if control{request["power_policy"].clone()}else{Value::Null}});
        new_file(&base.dir.join("eval-session.json"), &serde_json::to_vec_pretty(&state).unwrap())?;
        Ok(Self {base, state:RefCell::new(state),charge:RefCell::new(None)})
    }

    pub fn resume(dir: &Path, serial: &str) -> Result<Self, String> {
        Self::resume_base(Session::resume_eval(dir, serial)?)
    }
    pub fn resume_loadop(dir:&Path,serial:&str)->Result<Self,String> {
        Self::resume_base(Session::resume_loadop(dir,serial)?)
    }
    fn resume_base(base:Session)->Result<Self,String> {
        let state: Value = serde_json::from_slice(&crate::confirmation_transport::bounded(&base.dir.join("eval-session.json"), 8*1024*1024)?)
            .map_err(|e| e.to_string())?;
        let id = base.eval_identity()?;
        let token = id["token"].as_str().ok_or("missing token")?;
        validate_parent_kind(&state,&id)?;
        if ["serial","token","session","evidence_origin","device_binding","lease_scope"].iter().any(|k|state["identity"][*k]!=id[*k])
            || state["remote"] != format!("/data/local/tmp/phonefarm-confirmation-gray-{token}")
            || state["trace"] != format!("/sys/kernel/tracing/instances/phonefarm_gray_{token}")
            || !matches!(state["deployment"].as_str(),Some("not_started"|"installing"|"ready"))
            || !matches!(state["trace_state"].as_str(),Some("not_started"|"created"|"removed"))
            || !state["tools_created"].is_boolean()
            || ["boot_id","native_dir"].iter().any(|k|!state["identity"][*k].is_null()&&state["identity"][*k]!=id[*k])
            || state.get("pending").is_none() || !state["pending"].is_null()
        { return Err("invalid or pending parent gray eval resources; independent reconciliation required".into()); }
        if !state["fan"].is_null() && (!matches!(state["fan"]["original"].as_str(),Some("0"|"1")) || !state["fan"]["changed"].is_boolean()) {
            return Err("invalid original fan state".into());
        }
        if control_parent(&state) {validate_control_rounds(&state)?;} else if scripted(&state) {script_plan(&state)?;}
        if owns_charging(&state) {validate_charging(&state["charging"],&id)?;if !state["fan"].is_null(){return Err("charge-conditioned session cannot own fan conditioning".into());}}
        Ok(Self {base,state:RefCell::new(state),charge:RefCell::new(None)})
    }

    fn update(&self, f: impl FnOnce(&mut Value)) -> Result<(), String> {
        let mut next = self.state.borrow().clone(); f(&mut next);
        crate::devlease::durable_replace(&self.base.dir.join("eval-session.json"), &serde_json::to_vec_pretty(&next).unwrap())?;
        *self.state.borrow_mut()=next; Ok(())
    }
    pub fn stage(&self, stage: &str) -> Result<(),String> { self.update(|s|s["stage"]=json!(stage)) }
    fn remote(&self)->String { self.state.borrow()["remote"].as_str().unwrap().into() }
    fn trace(&self)->String { self.state.borrow()["trace"].as_str().unwrap().into() }
    fn clear(&self)->Result<(),String> {
        self.base.eval_ready()?;
        if !self.state.borrow()["pending"].is_null() {return Err("parent resource mutation completion is unrecorded".into());} Ok(())
    }
    pub fn read(&self, op:&str, command:&str)->Result<String,String> {
        self.clear()?;
        Ok(self.base.eval_transport(op,&["shell".into(),format!("su -c {}",q(command))],30_000,false)?.stdout)
    }
    fn mutate(&self, op:&str, args:&[String], ms:u64, done:impl FnOnce(&mut Value))->Result<String,String> {
        self.clear()?;
        self.update(|s|s["pending"]=json!(op))?;
        let result=self.base.eval_transport(op,args,ms,true)?;
        self.update(|s|{done(s);s["pending"]=Value::Null;})?;
        Ok(result.stdout)
    }
    fn write(&self, op:&str, cmd:&str, done:impl FnOnce(&mut Value))->Result<String,String> {
        self.mutate(op,&["shell".into(),format!("su -c {}",q(cmd))],30_000,done)
    }
    fn owned(&self)->Result<String,String> {
        let id=self.base.eval_identity()?;
        let boot=id["boot_id"].as_str().ok_or("missing boot identity")?;
        Ok(format!("test \"$(cat {}/owner)\" = {} && test \"$(cat /proc/sys/kernel/random/boot_id)\" = {}",
            q(&self.remote()),q(&format!("{} {boot}",id["token"].as_str().unwrap())),q(boot)))
    }
    pub fn capabilities(&self)->Result<crate::qualification::Capabilities,String> {
        let uid=self.read("eval_root","id -u")?;
        let available=self.read("eval_trace_capability","if [ -r /sys/kernel/tracing/events/kgsl/adreno_cmdbatch_submitted/format ] && [ -r /sys/kernel/tracing/events/kgsl/adreno_cmdbatch_retired/format ] && [ -d /sys/kernel/tracing/instances ]; then echo AVAILABLE; else echo UNAVAILABLE; fi")?;
        Ok(crate::qualification::Capabilities {backend:"adb".into(),root:Some(uid.trim()=="0"),
            trace_available:Some(available.trim()=="AVAILABLE"),measurement_available:Some(available.trim()=="AVAILABLE"),control_verified:None})
    }
    pub fn deploy(&self)->Result<(),String> {
        let id=self.base.eval_identity()?;
        let remote=self.remote(); let trace=self.trace();
        if self.base.eval_present(&remote)? || self.base.eval_present(&trace)? {return Err("owned eval resource already exists; refusing overwrite".into());}
        self.update(|s|{s["identity"]=id.clone();s["deployment"]=json!("installing");})?;
        let owner=format!("{} {}",id["token"].as_str().unwrap(),id["boot_id"].as_str().ok_or("missing boot")?);
        self.mutate("eval_tools_directory",&["shell".into(),format!("set -eu; command -v setsid; test ! -e {d}; mkdir {d}; printf '%s\\n' {owner} > {d}/owner; sync",d=q(&remote),owner=q(&owner))],30_000,|s|s["tools_created"]=json!(true))?;
        let control=control_parent(&self.state.borrow());
        let script = if !control && scripted(&self.state.borrow()) {Some(script_plan(&self.state.borrow())?)} else {None};
        let input = script.as_ref().map(|p|p.shell());
        let sampler = if control || script.is_some() {Some(crate::gray_script::sampler_shell())} else {None};
        let mut tools = vec![("worker.sh",WORKER),("bootstrap.sh",BOOTSTRAP),
            ("sample_env.sh",sampler.as_deref().unwrap_or(SAMPLER))];
        if !control {tools.push(("workload.sh",input.as_deref().unwrap_or(TRACE)));}
        if control || script.is_some() {tools.extend([("trace.sh",TRACE),("gray_child_guard.sh",CHILD_GUARD)]);}
        for (name,text) in tools {
            let local=self.base.dir.join(name);new_file(&local,text.as_bytes())?;
            self.mutate("eval_push_tool",&["push".into(),local.to_string_lossy().into(),format!("{remote}/{name}")],30_000,|_|{})?;
        }
        self.update(|s|s["deployment"]=json!("ready"))?;
        self.write("eval_trace_create",&format!("{} && test ! -e {t} && mkdir {t}",self.owned()?,t=q(&trace)),|s|s["trace_state"]=json!("created"))?;
        if !self.base.eval_present(&trace)? {return Err("created trace instance absent".into());}
        Ok(())
    }
    fn worker(&self,mode:&str,run:&str,seconds:Option<u64>,mutation:bool)->Result<String,String> {
        self.worker_with_qualification(mode,run,seconds,mutation,None)
    }
    fn worker_with_qualification(&self,mode:&str,run:&str,seconds:Option<u64>,mutation:bool,qualify:Option<bool>)->Result<String,String> {
        let id=self.base.eval_identity()?;
        let mut extra=seconds.map(|s|format!(" {s} {}",q(&self.trace()))).unwrap_or_default();
        if let Some(qualify)=qualify {extra.push_str(if qualify {" 1"} else {" 0"});}
        let timeout=if qualify.is_some() {round_plan(&self.state.borrow(),run)?.duration_ms().max((seconds.unwrap_or(0)+10)*1000)+120_000} else {(seconds.unwrap_or(0)+120)*1000};
        let cmd=format!("setsid sh {d}/worker.sh {d} {token} {boot} {mode} {run}{extra}",d=q(&self.remote()),
            token=q(id["token"].as_str().unwrap()),boot=q(id["boot_id"].as_str().ok_or("missing boot")?),mode=q(mode),run=q(run));
        let args=["shell".into(),format!("su -c {}",q(&cmd))];
        if mutation {self.mutate(mode,&args,timeout,|_|{})}
        else {self.clear()?; Ok(self.base.eval_transport(mode,&args,30_000,false)?.stdout)}
    }
    pub fn sample(&self,label:&str,seconds:u64,out:&Path)->Result<(bool,bool),String> {
        if !(1..=86400).contains(&seconds) || label.is_empty() || !label.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_') {return Err("invalid finite gray sample".into());}
        std::fs::create_dir_all(out).map_err(|e|e.to_string())?;
        self.update(|s|s["rounds"].as_array_mut().unwrap().push(json!({"label":label,"seconds":seconds,"out":out})))?;
        let result=self.worker("gray_sample",label,Some(seconds),true)?;
        new_file(&out.join("capture.log"),result.as_bytes())?;
        if result.trim()!="GRAY_SAMPLE_COMPLETE" {return Err("gray sample lifecycle receipt missing".into());}
        let mut wc=None;let mut sc=None;
        // Retrieve failure evidence too; a missing trace is explicit after child status is saved.
        for (remote,local) in [("workload_exit.txt","workload_exit.txt"),("sampler_exit.txt","sampler_exit.txt"),("quiescence.txt","quiescence.txt"),("sampler.log","env.txt"),("workload.log","workload.log")] {
            let value=self.read("eval_sample_evidence",&format!("{} && cat {}/{label}/{remote}",self.owned()?,q(&self.remote())))?;
            new_file(&out.join(local),value.as_bytes())?;
            match remote {"workload_exit.txt"=>wc=Some(value.trim()=="0"),"sampler_exit.txt"=>sc=Some(value.trim()=="0"),"quiescence.txt" if value.trim()!="OWNED_GROUPS_ABSENT"=>return Err("sample groups not absent".into()),_=>{}}
        }
        if wc==Some(true) {
            let trace=self.read("eval_trace_evidence",&format!("{} && cat {}/{label}/trace.txt",self.owned()?,q(&self.remote())))?;
            new_file(&out.join("trace.txt"),trace.as_bytes())?;
        }
        Ok((wc==Some(true),sc==Some(true)))
    }
    fn round_text(&self,label:&str,name:&str)->Result<String,String> {
        if matches!(self.state.borrow()["schema"].as_str(),Some(LOADOP_SCHEMA|CONTROL_SCHEMA|INPUT_CONTROL_SCHEMA|LEGACY_CONTROL_SCHEMA)) && !self.base.eval_present(&format!("{}/{label}/{name}",self.remote()))? {
            return Err(format!("{label}/{name}: expected collection evidence absent; original remote ledger retained"));
        }
        self.read("eval_sample_evidence",&format!("{} && cat {}/{}/{}",self.owned()?,q(&self.remote()),q(label),q(name)))
    }
    fn round_png(&self,label:&str,name:&str,out:&Path)->Result<(),String> {
        self.clear()?;
        let command=format!("{} && cat {}/{}/{}",self.owned()?,q(&self.remote()),q(label),q(name));
        let result=self.base.eval_transport("eval_sample_png",&["exec-out".into(),"su".into(),"-c".into(),command],30_000,false)?;
        new_file(out,&crate::confirmation_transport::bounded(&result.stdout_path,8*1024*1024)?)
    }
    pub fn sample_script(&self,label:&str,seconds:u64,out:&Path,qualify:bool)->Result<(bool,bool,Value),String> {
        let plan=script_plan(&self.state.borrow())?;
        if seconds!=plan.seconds || label.is_empty() || !label.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_') {return Err("invalid finite gray sample".into());}
        std::fs::create_dir_all(out).map_err(|e|e.to_string())?;
        self.update(|s|s["rounds"].as_array_mut().unwrap().push(json!({"label":label,"seconds":seconds,"out":out,"qualify":qualify})))?;
        self.collect_script(label,seconds,out,qualify,&plan,"gray_script_sample")
    }
    pub fn control_health(&self)->Result<(),String> {
        self.clear()?;
        if owns_charging(&self.state.borrow()) {self.owned_charge()?;}
        let deployment=self.state.borrow()["deployment"].as_str().map(str::to_owned);
        match deployment.as_deref() {
            Some("not_started")=>Ok(()),
            Some("ready")=>self.admission_ready(),
            _=>Err("control resource deployment is incomplete".into()),
        }
    }
    pub fn sample_current(&self,label:&str,plan:&crate::gray_script::Plan,input_kind:&str,out:&Path,before_launch:impl FnOnce()->Result<(),String>)->Result<(bool,bool,Value),String> {
        if self.state.borrow()["schema"]!=CONTROL_SCHEMA || !valid_round(label) {return Err("invalid control sampling parent/round".into());}
        let seconds=plan.seconds;
        let normalized=match input_kind {
            "idle"=>crate::gray_script::Plan::control(None,seconds)?,
            "finite"=>crate::gray_script::Plan::control(Some(&plan.to_value()["steps"]),seconds)?,
            _=>return Err("unknown control input kind".into()),
        };
        if normalized!=*plan {return Err("control input plan is not normalized for its declared kind".into());}
        std::fs::create_dir_all(out).map_err(|e|e.to_string())?;
        let policy=control_power_policy(&self.state.borrow())?;
        let mut power=json!({"policy":policy,"before":null,"after":null,"error":null});
        let power_path=out.join("power.json");
        new_file(&power_path,&serde_json::to_vec_pretty(&power).unwrap())?;
        let mut attempted=false;
        let result=(|| {
        self.control_health()?;
        if self.state.borrow()["deployment"]=="not_started" {
            let caps=self.capabilities()?;
            if caps.root!=Some(true) || caps.trace_available!=Some(true) {return Err("current-scene sampling requires root and owned KGSL trace support".into());}
            self.deploy()?;
        }
        let script_name=format!("workload-{label}.sh");
        if self.state.borrow()["rounds"].as_array().unwrap().iter().any(|r|r["label"]==label) {return Err("control sample round already exists; refusing replay".into());}
        // Record this exact plan before any upload. R remains absent for the
        // worker's exclusive mkdir; scripts are flat files outside round dirs.
        self.update(|s|s["rounds"].as_array_mut().unwrap().push(json!({"label":label,"seconds":seconds,"out":out,"qualify":false,"input_plan":plan.to_value(),"input_kind":input_kind,"workload":script_name,"deployment":"recorded"})))?;
        new_file(&out.join("input-plan.json"),&serde_json::to_vec_pretty(&plan.to_value()).unwrap())?;
        let local=out.join(&script_name);new_file(&local,plan.shell().as_bytes())?;
        let remote=format!("{}/{script_name}",self.remote());
        if self.base.eval_present(&remote)? {return Err("control round workload path already exists".into());}
        self.mutate("control_push_workload",&["push".into(),local.to_string_lossy().into(),remote],30_000,|s| {
            s["rounds"].as_array_mut().unwrap().iter_mut().find(|r|r["label"]==label).unwrap()["deployment"]=json!("ready");
        })?;
        if policy=="battery" {
            let fresh=self.state.borrow()["charging"]["stage"]=="not_started";
            let previous=self.state.borrow()["charging"]["last_observation"].clone();
            let before=if fresh {self.prepare_charging()}else{self.charging_observation()};
            // Retain the observed battery state even when it failed admission.
            match before {
                Ok(value)=>power["before"]=public_charge_observation(&value),
                Err(error)=>{
                    let value=self.state.borrow()["charging"]["last_observation"].clone();
                    if value!=previous {power["before"]=public_charge_observation(&value);}
                    return Err(format!("power before: {error}"));
                },
            }
        }
        crate::devlease::durable_replace(&power_path,&serde_json::to_vec_pretty(&power).unwrap())?;
        // Deployment may take longer than the image freshness window. Image
        // geometry and remaining budget must be rechecked at this boundary.
        // A rejected admission leaves only an uploaded script, never a permit.
        before_launch()?;
        attempted=true;
        self.collect_script(label,seconds,out,false,plan,"gray_control_sample")
        })();
        let mut errors=Vec::new();
        if let Err(error)=&result {errors.push(self.public_power_error(error));}
        if policy=="battery" && attempted {
            // This uses the existing pending/sticky checks. An uncertain worker
            // is never followed by an unguarded read or restoration attempt.
            let previous=self.state.borrow()["charging"]["last_observation"].clone();
            let after=self.charging_observation();
            if let Ok(value)=&after {power["after"]=public_charge_observation(value);}
            else if let Err(error)=after {
                let value=self.state.borrow()["charging"]["last_observation"].clone();
                if value!=previous {power["after"]=public_charge_observation(&value);}
                errors.push(format!("power after: {}",self.public_power_error(&error)));
            }
        }
        if !errors.is_empty() {power["error"]=json!(errors.join("; "));}
        if let Err(error)=crate::devlease::durable_replace(&power_path,&serde_json::to_vec_pretty(&power).unwrap()) {
            errors.push(format!("power evidence save failed: {}",self.public_power_error(&error)));
            power["error"]=json!(errors.join("; "));
        }
        // A failed post-observation must not discard successfully collected
        // trace/sampler/input evidence. The result and file expose its failure.
        match result {
            Ok((trace,sampler,mut input))=>{input["power"]=power;Ok((trace,sampler,input))},
            Err(error)=>Err(error),
        }
    }
    pub fn control_power_state(&self)->Result<Value,String> {
        control_power_snapshot(&self.state.borrow())
    }
    fn public_power_error(&self,error:&str)->String {
        let state=self.state.borrow();
        let mut text=error.replace(&self.base.dir.to_string_lossy().to_string(),"<session>");
        for value in [&state["remote"],&state["trace"],&state["charging"]["record_path"],&state["identity"]["token"]] {
            if let Some(secret)=value.as_str().filter(|v|!v.is_empty()) {text=text.replace(secret,"<owned>");}
        }
        text
    }
    fn collect_script(&self,label:&str,seconds:u64,out:&Path,qualify:bool,plan:&crate::gray_script::Plan,mode:&str)->Result<(bool,bool,Value),String> {
        let result=self.worker_with_qualification(mode,label,Some(seconds),true,Some(qualify))?;
        new_file(&out.join("capture.log"),result.as_bytes())?;
        if result.trim()!="GRAY_SCRIPT_SAMPLE_COMPLETE" {return Err("gray script lifecycle receipt missing".into());}
        let mut data=serde_json::Map::new();
        for (name,local) in [("workload_exit.txt","workload_exit.txt"),("trace_exit.txt","trace_exit.txt"),("sampler_exit.txt","sampler_exit.txt"),
            ("quiescence.txt","quiescence.txt"),("workload.log","workload.log"),("trace.log","trace.log"),("sampler.log","env.txt"),
            ("workload-time.json","workload-time.json"),("trace-time.json","trace-time.json"),("sampler-time.json","sampler-time.json")] {
            let text=self.round_text(label,name)?;new_file(&out.join(local),text.as_bytes())?;
            data.insert(name.into(),if name.ends_with(".json"){serde_json::from_str(&text).map_err(|e|format!("{name}: {e}"))?}else{json!(text.trim())});
        }
        let receipt=self.natural_input(label,plan,Some(&out.join("input-complete.json")))?;
        if data["quiescence.txt"]!="OWNED_GROUPS_ABSENT" {return Err("script sample groups not absent".into());}
        let trace_ok=data["trace_exit.txt"]=="0";
        let sampler_ok=data["sampler_exit.txt"]=="0";
        if trace_ok {new_file(&out.join("trace.txt"),self.round_text(label,"trace.txt")?.as_bytes())?;}
        if qualify {for name in ["frame_a.png","frame_b.png"] {self.round_png(label,name,&out.join(name))?;}}
        let mut evidence=json!({"plan":plan.to_value(),"natural_completion":receipt,"actual":data,"trace_lead_s":6,"sampler_seconds":seconds+10});
        if control_parent(&self.state.borrow()) {
            evidence["input_kind"]=self.state.borrow()["rounds"].as_array().unwrap().iter().find(|r|r["label"]==label).and_then(|r|r["input_kind"].as_str()).map(|kind|json!(kind)).unwrap_or_else(||json!("idle"));
        }
        new_file(&out.join("input-window.json"),&serde_json::to_vec_pretty(&evidence).unwrap())?;
        validate_input_window(&evidence)?;
        Ok((trace_ok,sampler_ok,evidence))
    }
    fn natural_input(&self,label:&str,plan:&crate::gray_script::Plan,destination:Option<&Path>)->Result<Value,String> {
        let path=format!("{}/{label}/input-complete.json",self.remote());
        if !self.base.eval_present(&path)? {return Err(format!("{label}: input natural completion receipt absent; touch release is unverified"));}
        let text=self.round_text(label,"input-complete.json")?;
        if let Some(path)=destination {new_file(path,text.as_bytes())?;}
        let receipt:Value=serde_json::from_str(&text).map_err(|e|format!("invalid input receipt: {e}"))?;
        let id=self.base.eval_identity()?;
        if receipt["schema"]!="phonefarm/gray-input-complete@1" || receipt["token"]!=id["token"] || receipt["boot_id"]!=id["boot_id"] || receipt["run"]!=label
            || receipt["declared_ms"].as_u64()!=Some(plan.duration_ms()) || receipt["steps"].as_u64()!=plan.to_value()["steps"].as_array().map(|a|a.len() as u64)
            || self.round_text(label,"workload_exit.txt")?.trim()!="0" {return Err(format!("{label}: input natural completion identity/status differs"));}
        time_window(&receipt)?;
        Ok(receipt)
    }
    fn verify_inputs(&self)->Result<(),String> {
        let state=self.state.borrow().clone();
        if !scripted(&state) {return Ok(());}
        for round in state["rounds"].as_array().ok_or("missing input rounds")? {
            let label=round["label"].as_str().filter(|s|!s.is_empty()&&s.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_')).ok_or("invalid input round label")?;
            let plan=round_plan(&state,label)?;
            // Called only after the worker has verified every current ledger and
            // group. Its protocol admits input only through this durable permit;
            // trace preparation may have failed before input was admitted.
            if self.base.eval_present(&format!("{}/{label}/permit-workload",self.remote()))? {self.natural_input(label,&plan,None)?;}
        }
        Ok(())
    }
    fn identity(&self)->Result<(),String> {
        let id=self.base.eval_identity()?;
        let boot=id["boot_id"].as_str().ok_or("missing original boot")?;
        if self.read("eval_resource_identity","cat /proc/sys/kernel/random/boot_id")?.trim()!=boot {return Err("device boot changed; parent resource restoration refused".into());}
        Ok(())
    }
    fn owned_charge(&self)->Result<Value,String> {
        if !owns_charging(&self.state.borrow()) {return Err("this parent does not own charging conditioning".into());}
        let charge=self.state.borrow()["charging"].clone();validate_charging(&charge,&self.base.eval_identity()?)?;Ok(charge)
    }
    fn battery(&self)->Result<crate::hwcond::BatteryState,String> {
        let root="/sys/class/power_supply/battery";
        let text=self.read("loadop_battery",&format!("s=$(cat {root}/status) && c=$(cat {root}/current_now) && p=$(cat {root}/capacity) && printf '%s\n%s\n%s\n' \"$s\" \"$c\" \"$p\""))?;
        let fields:Vec<_>=text.lines().collect();
        if fields.len()!=3 || !matches!(fields[0],"Charging"|"Discharging"|"Not charging"|"Full") {return Err("battery status is not a recognized three-field observation".into());}
        let current=fields[1].trim().parse::<i64>().map_err(|_|"battery current is unknown")?;
        let capacity=fields[2].trim().parse::<i64>().ok().filter(|v|(0..=100).contains(v)).ok_or("battery capacity is unknown")?;
        Ok(crate::hwcond::BatteryState{status:fields[0].into(),current_ua:Some(current),capacity_pct:Some(capacity)})
    }
    pub fn prepare_charging(&self)->Result<Value,String> {
        let charge=self.owned_charge()?;self.identity()?;
        if charge["stage"]!="not_started" || self.state.borrow()["deployment"]!="ready" {return Err("charging preparation requires a fresh deployed charge-conditioned session".into());}
        let path=Path::new(charge["record_path"].as_str().unwrap());
        if crate::hwcond::charge_record(path)?.is_some() {return Err("foreign charging record appeared before conditioning".into());}
        let initial=self.battery()?;
        self.update(|s|s["charging"]["initial"]=battery_value(&initial))?;
        if initial.on_battery() {
            self.update(|s|s["charging"]["stage"]=json!("observed_no_mutation"))?;
            return self.charging_observation();
        }
        let phone=ChargeShell::new(self);
        let guard=crate::hwcond::ChargeSuspend::apply_with_hooks(&phone,path,
            |node,saved,want|self.bind_charge(path,node,saved,want),
            ||self.update(|s|s["charging"]["stage"]=json!("restoring")),
            ||self.charge_restored("candidate_rollback"))?;
        *self.charge.borrow_mut()=Some(guard);
        self.update(|s|s["charging"]["stage"]=json!("suspended"))?;
        self.charging_observation()
    }
    fn bind_charge(&self,path:&Path,node:&str,saved:&str,want:&str)->Result<(),String> {
        if crate::hwcond::charge_record(path)?!=Some((node.into(),saved.into())) {return Err("new charging record does not contain the saved original values".into());}
        let id=self.base.eval_identity()?;
        let evidence=self.base.dir.join(format!("charge-record-{}-{}.txt",std::process::id(),chrono::Utc::now().timestamp_micros()));
        new_file(&evidence,&crate::confirmation_transport::bounded(path,65536)?)?;
        let binding=json!({"serial":id["serial"],"token":id["token"],"session":self.base.dir,"record_path":path,
            "node":node,"original":saved,"experiment":want,"record_evidence":evidence,"recorded_at":chrono::Utc::now().to_rfc3339()});
        self.update(|s|{s["charging"]["binding"]=binding;s["charging"]["stage"]=json!("recorded");})
    }
    pub fn charging_observation(&self)->Result<Value,String> {
        let charge=self.owned_charge()?;self.identity()?;
        if !matches!(charge["stage"].as_str(),Some("suspended"|"observed_no_mutation")) {return Err("owned charging is not prepared for collection".into());}
        let path=Path::new(charge["record_path"].as_str().unwrap());
        let control=if charge["stage"]=="suspended" {
            let binding=&charge["binding"];
            if crate::hwcond::charge_record(path)?!=Some((binding["node"].as_str().unwrap().into(),binding["original"].as_str().unwrap().into())) {return Err("charging record no longer matches this session".into());}
            let node=binding["node"].as_str().unwrap();
            let current=ChargeShell::new(self).node(node)?.ok_or("owned charging control disappeared")?;
            if Some(current.as_str())!=binding["experiment"].as_str() {return Err("charging control readback differs from the session experiment value".into());}
            json!({"node":node,"original":binding["original"],"experiment":binding["experiment"],"readback":current})
        } else {
            if crate::hwcond::charge_record(path)?.is_some(){return Err("unexpected charge record in observed-only session".into());}Value::Null
        };
        let battery=self.battery()?;
        let value=json!({"stage":charge["stage"],"control_modified_by_session":charge["stage"]=="suspended","control":control,
            "battery":battery_value(&battery),"on_battery":battery.on_battery(),"observed_at":chrono::Utc::now().to_rfc3339(),"record_path":path});
        self.update(|s|s["charging"]["last_observation"]=value.clone())?;
        if !battery.on_battery() {return Err(format!("battery policy requires actual discharging for sampling: {}",battery.why_not_on_battery()));}
        Ok(value)
    }
    fn charge_restored(&self,reason:&str)->Result<(),String> {
        self.update(|s|{
            let mut binding=s["charging"]["binding"].clone();binding["restored_at"]=json!(chrono::Utc::now().to_rfc3339());binding["reason"]=json!(reason);binding["original_readback_verified"]=json!(true);
            s["charging"]["history"].as_array_mut().unwrap().push(binding);
            s["charging"]["binding"]=Value::Null;s["charging"]["stage"]=json!(if reason=="candidate_rollback"{"unmodified"}else{"restored"});
        })
    }
    fn restore_charging(&self)->Result<(),String> {
        let charge=self.owned_charge()?;
        let path=Path::new(charge["record_path"].as_str().unwrap());
        match charge["stage"].as_str() {
            Some("not_started"|"observed_no_mutation"|"unmodified"|"restored")=>{
                if crate::hwcond::charge_record(path)?.is_some() {return Err("unowned charging record present during restoration".into());}return Ok(());
            },
            Some("recorded"|"suspended"|"restoring")=>{},
            _=>return Err("unknown charging recovery stage".into()),
        }
        let binding=&charge["binding"];
        // owned_charge validated the persisted original record before reuse.
        let mut guard=match self.charge.borrow_mut().take() {
            Some(guard)=>guard,
            None=>crate::hwcond::ChargeSuspend::from_bound_record(path,binding["node"].as_str().unwrap(),binding["original"].as_str().unwrap(),charge["stage"]=="restoring")?,
        };
        self.update(|s|s["charging"]["stage"]=json!("restoring"))?;
        if !guard.restore_checked(&ChargeShell::new(self))? {*self.charge.borrow_mut()=Some(guard);return Err("owned charging restoration readback differs; record retained".into());}
        self.charge_restored("session_finish")
    }
    pub fn fan_off(&self)->Result<Value,String> {
        if matches!(self.state.borrow()["schema"].as_str(),Some(LOADOP_SCHEMA|CONTROL_SCHEMA|INPUT_CONTROL_SCHEMA|LEGACY_CONTROL_SCHEMA)) {return Err("this parent does not own fan conditioning".into());}
        if self.state.borrow()["deployment"]!="ready" {return Err("fan conditioning requires deployed eval resources".into());}
        self.identity()?;
        if !self.base.eval_present(FAN)? {return Ok(json!({"before":null,"after":null,"forced":false}));}
        let before=self.read("eval_fan_read",&format!("cat {FAN}"))?.trim().to_owned();
        if !matches!(before.as_str(),"0"|"1") {return Err("unrecognized fan value".into());}
        if self.state.borrow()["fan"].is_null() {self.update(|s|s["fan"]=json!({"original":before,"changed":false}))?;}
        if before!="0" {self.write("eval_fan_off",&format!("printf '0\\n' > {FAN}"),|s|s["fan"]["changed"]=json!(true))?;}
        let after=self.read("eval_fan_readback",&format!("cat {FAN}"))?.trim().to_owned();
        if after!="0" {return Err("fan off readback differs".into());}
        Ok(json!({"before":before,"after":after,"forced":before!="0"}))
    }
    pub fn wait_cool(&self,target:Option<f64>,timeout:u64)->Result<Value,String> {
        let start=Instant::now();
        loop {
            let raw=self.read("eval_temperature","for z in /sys/class/thermal/thermal_zone*; do [ -d \"$z\" ] || continue; t=$(cat \"$z/type\") || exit; v=$(cat \"$z/temp\") || exit; printf '%s %s\\n' \"$t\" \"$v\"; done")?;
            let best=raw.lines().filter_map(|line|{let mut p=line.split_whitespace();let z=p.next()?;let t=p.next()?.parse::<i64>().ok()?;
                ((z.starts_with("cpu-")||z.starts_with("cpullc")||z.starts_with("gpuss"))&&(0<t&&t<100000)).then(||(z.to_owned(),t))}).max_by_key(|(_,t)|*t);
            let Some((zone,t))=best else {return Ok(json!({"ok":false,"c":null,"timeout":false,"note":"读不到 SoC 热区"}));};
            let c=t as f64/1000.0; let elapsed=start.elapsed().as_secs();
            if target.is_none_or(|v|c<v) || elapsed>=timeout {return Ok(json!({"ok":target.is_some_and(|v|c<v),"zone":zone,"c":c,"waited_s":elapsed,"timeout":target.is_some_and(|v|c>=v)}));}
            std::thread::sleep(Duration::from_secs(8));
        }
    }
    pub fn admission_ready(&self)->Result<(),String> {
        self.clear()?;
        if self.worker("verify_gray_idle","verify",None,false)?.trim()!="GRAY_IDLE_QUIESCENT" {return Err("gray baseline children not currently quiescent".into());}
        self.verify_inputs()?;
        if self.read("eval_trace_stopped",&format!("cat {}/tracing_on",q(&self.trace())))?.trim()!="0" {return Err("trace still active".into());}
        Ok(())
    }
    fn close_resources(&self)->Result<Value,String> {
        let state=self.state.borrow().clone();
        if state["deployment"]=="not_started" && state["trace_state"]=="not_started" && state["fan"].is_null() && state["pending"].is_null() {
            if owns_charging(&state) {self.restore_charging()?;}
            return Ok(json!({"no_parent_device_mutations":true,"remote_session_closed":null,"fan_restored":null}));
        }
        self.clear()?;self.identity()?;
        if owns_charging(&state) && state["deployment"]=="ready" {
            // Group proof is independent of input natural completion. Restore
            // charging before the stronger closure check can fail on input.
            if self.worker("verify_gray_groups","verify",None,false)?.trim()!="GRAY_GROUPS_QUIESCENT" {return Err("owned groups not currently quiescent; charging retained".into());}
            self.restore_charging()?;
        }
        match state["deployment"].as_str() {
            Some("ready")=>{
                let closed=self.read("eval_closed",&format!("{} && if [ -d {}/session.closed ]; then echo CLOSED; else echo OPEN; fi",self.owned()?,q(&self.remote())))?;
                if closed.trim()=="OPEN" {
                    let run=format!("close{}_{}",std::process::id(),chrono::Utc::now().timestamp_micros());
                    if self.worker("reconcile_bench",&run,None,true)?.trim()!="BENCH_RECONCILE_COMPLETE" {return Err("eval child closure receipt missing".into());}
                } else if closed.trim()!="CLOSED" {return Err("unknown eval closure state".into());}
                if self.worker("verify_bench","verify",None,false)?.trim()!="BENCH_CLOSED_QUIESCENT" {return Err("eval remote lifecycle not quiescent".into());}
            },
            Some("installing") if state["rounds"].as_array().is_some_and(|a|a.is_empty()) => {
                // A known partial upload has never launched a worker. Close its
                // owned directory directly; the worker may not have arrived yet.
                if state["tools_created"]==true {
                    let closed=self.read("eval_partial_closed",&format!("{} && if [ -d {}/session.closed ]; then echo CLOSED; else echo OPEN; fi",self.owned()?,q(&self.remote())))?;
                    let id=self.base.eval_identity()?;
                    let owner=format!("{} {} partial",id["token"].as_str().unwrap(),id["boot_id"].as_str().unwrap());
                    if closed.trim()=="OPEN" {self.write("eval_partial_close",&format!("{} && mkdir {d}/session.closed && printf '%s\\n' {owner} > {d}/session.closed/owner && sync",self.owned()?,d=q(&self.remote()),owner=q(&owner)),|_|{})?;}
                    else if closed.trim()!="CLOSED" {return Err("unknown partial deployment closure".into());}
                    if self.read("eval_partial_owner",&format!("{} && cat {}/session.closed/owner",self.owned()?,q(&self.remote())))?.trim()!=owner {return Err("partial closure owner differs".into());}
                }
            },
            _=>return Err("invalid eval resource lifecycle".into()),
        }
        self.verify_inputs()?;
        if state["trace_state"]=="created" {
            self.write("eval_trace_stop",&format!("{} && printf '0\\n' > {}/tracing_on",self.owned()?,q(&self.trace())),|_|{})?;
            if self.read("eval_trace_stop_readback",&format!("cat {}/tracing_on",q(&self.trace())))?.trim()!="0" {return Err("trace stop readback differs".into());}
            self.write("eval_trace_remove",&format!("{} && rmdir {}",self.owned()?,q(&self.trace())),|s|s["trace_state"]=json!("removed"))?;
        }
        if state["trace_state"]!="not_started" && self.base.eval_present(&self.trace())? {return Err("owned trace instance remains".into());}
        if state["fan"]["changed"]==true {
            let original=state["fan"]["original"].as_str().ok_or("missing original fan")?;
            self.write("eval_fan_restore",&format!("printf '%s\\n' {} > {FAN}",q(original)),|_|{})?;
            if self.read("eval_fan_restore_readback",&format!("cat {FAN}"))?.trim()!=original {return Err("fan restoration readback differs".into());}
        }
        Ok(json!({"remote_session_closed":state["tools_created"]==true,"owned_trace_absent":true,"fan_restored":true,
            "remote":self.remote(),"tool_policy":"retain closed ownership ledger, scripts and round evidence","charging_or_frequency_changed":if owns_charging(&state){Value::Null}else{json!(false)},"frequency_changed":false,"charging":self.state.borrow()["charging"]}))
    }
    pub fn finish(&self,error:Option<&str>)->Result<Value,String> {
        let resources=self.close_resources();
        // Never restore the layer while an unverified sampler/workload can still be running.
        let base=match &resources {Ok(_)=>self.base.restore_for_eval(error),Err(e)=>Err(e.clone())};
        let id=self.base.eval_identity()?;
        let path=self.base.dir.join(format!("eval-recovery-{}-{}.json",std::process::id(),chrono::Utc::now().timestamp_micros()));
        let verified=resources.is_ok() && base.as_ref().is_ok_and(|v|v["status"]=="verified");
        let (schema,scope)=crate::gray_session::parent_recovery_contract(id["completion_owner"].as_str().ok_or("missing completion owner")?)?;
        let mut value=json!({"schema":schema,"status":if verified{"verified"}else{"required"},
            "scope":scope,"serial":id["serial"],"token":id["token"],"session":self.base.dir,
            "evidence_origin":id["evidence_origin"],"base_recovery":id["base_recovery"],"base":base.as_ref().ok(),
            "resources":resources.as_ref().ok(),"charging":self.state.borrow()["charging"],"error":resources.as_ref().err().or(base.as_ref().err()),
            "experiment_error":error,"game_state_restored":false,"report_path":path});
        new_file(&path,&serde_json::to_vec_pretty(&value).unwrap())?;
        if verified {self.base.complete_eval(&path)?;value["status"]=json!("reported_complete");}
        Ok(value)
    }
}

fn battery_value(battery:&crate::hwcond::BatteryState)->Value {
    json!({"status":battery.status,"current_now_ua":battery.current_ua,"capacity_pct":battery.capacity_pct,"on_battery":battery.on_battery()})
}
fn validate_charging(charge:&Value,id:&Value)->Result<(),String> {
    let serial=id["serial"].as_str().ok_or("missing charge serial")?;
    let path=crate::hwcond::lock_state_path("charge",&Some(serial.into()))?;
    if charge["record_path"]!=json!(path) || charge["history"].as_array().is_none()
        || !matches!(charge["stage"].as_str(),Some("not_started"|"recorded"|"suspended"|"restoring"|"restored"|"unmodified"|"observed_no_mutation")) {
        return Err("invalid parent charging obligation".into());
    }
    let bound=matches!(charge["stage"].as_str(),Some("recorded"|"suspended"|"restoring"));
    if bound {
        let b=&charge["binding"];
        let node=b["node"].as_str().ok_or("missing original charge node")?;
        let saved=b["original"].as_str().ok_or("missing original charge value")?;
        if ["serial","token","session"].iter().any(|k|b[*k]!=id[*k]) || b["record_path"]!=charge["record_path"]
            || crate::hwcond::parse_charge_ctl_state(&crate::hwcond::charge_ctl_state_text(node,saved)).is_none()
            || b["experiment"].as_str().is_none_or(|v|v.parse::<i64>().is_err())
            || b["record_evidence"].as_str().is_none_or(|p|Path::new(p).parent()!=id["session"].as_str().map(Path::new)) {
            return Err("charging original values are not bound to this session".into());
        }
        match crate::hwcond::charge_record(&path)? {
            Some(values) if values==(node.into(),saved.into())=>{},
            None if charge["stage"]=="restoring"=>{},
            _=>return Err("charging record differs from the bound original values".into()),
        }
    } else if !charge["binding"].is_null() {return Err("inactive charging obligation retains an unknown binding".into());}
    Ok(())
}
struct ChargeShell<'a> {parent:&'a Parent,error:RefCell<Option<String>>}
impl<'a> ChargeShell<'a> {
    fn new(parent:&'a Parent)->Self {Self{parent,error:RefCell::new(None)}}
    fn ready(&self)->Result<(),String> {
        self.parent.clear()?;
        if let Some(error)=self.error.borrow().as_ref(){return Err(error.clone());}Ok(())
    }
    fn execute(&self,command:&str,ms:u64,mutation:bool)->Result<String,String> {
        self.ready()?;
        let id=self.parent.base.eval_identity()?;
        let boot=id["boot_id"].as_str().ok_or("missing charge boot identity")?;
        let command=format!("test \"$(cat /proc/sys/kernel/random/boot_id)\" = {} && {command}",q(boot));
        let args=["shell".into(),command];
        if mutation {self.parent.mutate("charge_control",&args,ms,|_|{})}
        else {Ok(self.parent.base.eval_transport("charge_read",&args,ms,false)?.stdout)}
    }
    fn node(&self,path:&str)->Result<Option<String>,String> {
        self.ready()?;
        if !crate::hwcond::CHARGE_CTL_CANDIDATES.iter().any(|n|n.path==path){return Err("unknown charging node".into());}
        // Successful directory enumeration proves absence. cat errors are never
        // converted into an absent/unsupported candidate.
        if !self.parent.base.eval_present(path)? {return Ok(None);}
        let value=self.execute(&format!("su -c {}",q(&format!("cat {}",q(path)))),8_000,false)?;
        let value=value.trim();
        if value.parse::<i64>().is_err(){return Err(format!("charging node {path} has an invalid value"));}
        Ok(Some(value.into()))
    }
    fn text_result(&self,result:Result<String,String>)->String {
        match result {Ok(value)=>value,Err(error)=>{*self.error.borrow_mut()=Some(error);String::new()}}
    }
}
impl crate::hwcond::Shell for ChargeShell<'_> {
    fn shell(&self,cmd:&str,ms:u64)->String {self.text_result(self.execute(cmd,ms,false))}
    fn shell_write(&self,cmd:&str,ms:u64)->String {self.text_result(self.execute(cmd,ms,true))}
    fn check(&self)->Result<(),String> {self.ready()}
    fn charge_node(&self,path:&str)->Result<Option<String>,String> {self.node(path)}
    fn charge_battery(&self)->Result<crate::hwcond::BatteryState,String> {self.ready()?;self.parent.identity()?;self.parent.battery()}
}

fn validate_parent_kind(state:&Value,id:&Value)->Result<(),String> {
    let valid=match id["completion_owner"].as_str() {
        Some("gray_eval_launch")=>state["schema"]==SCHEMA && (state["identity"]["completion_owner"].is_null()||state["identity"]["completion_owner"]=="gray_eval_launch"),
        Some("gray_eval_script")=>state["schema"]==SCRIPT_SCHEMA && state["identity"]["completion_owner"]=="gray_eval_script",
        Some("gray_ab_loadop")=>state["schema"]==LOADOP_SCHEMA && state["identity"]["completion_owner"]=="gray_ab_loadop",
        Some("gray_layer_control")=>control_parent(state) && state["identity"]["completion_owner"]=="gray_layer_control"
            && state["schema"]==id["control_parent_schema"]
            && (state["identity"]["control_parent_schema"]==id["control_parent_schema"]
                || (state["schema"]==LEGACY_CONTROL_SCHEMA && state["identity"]["control_parent_schema"].is_null())),
        _=>false,
    };
    if valid {Ok(())}else{Err("parent schema/completion owner mismatch".into())}
}
fn control_parent(state:&Value)->bool {matches!(state["schema"].as_str(),Some(CONTROL_SCHEMA|INPUT_CONTROL_SCHEMA|LEGACY_CONTROL_SCHEMA))}
fn control_power_policy(state:&Value)->Result<&'static str,String> {
    match state["schema"].as_str() {
        Some(CONTROL_SCHEMA)=>match state["power_policy"].as_str() {
            Some("observe")=>Ok("observe"),Some("battery")=>Ok("battery"),
            _=>Err("control parent has no recognized fixed power policy".into()),
        },
        Some(INPUT_CONTROL_SCHEMA|LEGACY_CONTROL_SCHEMA) if state["power_policy"].is_null()=>Ok("observe"),
        _=>Err("control parent power policy/schema mismatch".into()),
    }
}
fn owns_charging(state:&Value)->bool {
    state["schema"]==LOADOP_SCHEMA || (state["schema"]==CONTROL_SCHEMA && state["power_policy"]=="battery")
}
fn charge_stage(value:&Value)->Option<&str> {
    value.as_str().filter(|v|matches!(*v,"not_started"|"recorded"|"suspended"|"restoring"|"restored"|"unmodified"|"observed_no_mutation"))
}
fn public_battery(value:&Value)->Value {
    if value.is_null() {return Value::Null;}
    json!({"status":value["status"].as_str().filter(|v|matches!(*v,"Charging"|"Discharging"|"Not charging"|"Full")),
        "current_ua":value["current_now_ua"].as_i64(),"capacity_pct":value["capacity_pct"].as_i64().filter(|v|(0..=100).contains(v)),
        "on_battery":value["on_battery"].as_bool()})
}
fn public_charge_observation(value:&Value)->Value {
    if value.is_null() {return Value::Null;}
    json!({"stage":charge_stage(&value["stage"]),"control_modified_by_session":value["control_modified_by_session"].as_bool(),
        "battery":public_battery(&value["battery"]),"on_battery":value["on_battery"].as_bool(),"observed_at":value["observed_at"].as_str()})
}
/// Host-only projection: recovery failures must remain visible even when the
/// device, live lease or original charge record cannot currently be checked.
pub(crate) fn control_power_snapshot(state:&Value)->Result<Value,String> {
    let policy=control_power_policy(state)?;
    let charge=&state["charging"];
    if policy=="observe" {
        if !charge.is_null() {return Err("observe policy cannot own a charging record".into());}
        return Ok(json!({"policy":policy,"stage":"unmodified","control_modified_by_session":false,"initial":null,"last_observation":null}));
    }
    let stage=charge_stage(&charge["stage"]).ok_or("invalid control charging stage")?;
    let history=charge["history"].as_array().ok_or("missing charging history")?;
    let modified=match stage {
        "recorded"|"restoring"=>Value::Null,
        "suspended"=>json!(true),
        _=>json!(!history.is_empty()),
    };
    Ok(json!({"policy":policy,"stage":stage,"control_modified_by_session":modified,
        "initial":public_battery(&charge["initial"]),"last_observation":public_charge_observation(&charge["last_observation"])}))
}
fn scripted(state:&Value)->bool {state["schema"]==SCRIPT_SCHEMA || state["schema"]==LOADOP_SCHEMA || control_parent(state)}
fn valid_round(label:&str)->bool {!label.is_empty() && label.len()<=64 && label.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_')}
fn round_plan(state:&Value,label:&str)->Result<crate::gray_script::Plan,String> {
    if !control_parent(state) {return script_plan(state);}
    let round=state["rounds"].as_array().ok_or("missing control rounds")?.iter().find(|r|r["label"]==label).ok_or("control round not recorded")?;
    let seconds=round["seconds"].as_u64().ok_or("missing control seconds")?;
    let plan=if state["schema"]==LEGACY_CONTROL_SCHEMA {
        if !round["input_kind"].is_null() && round["input_kind"]!="idle" {return Err("legacy control parent cannot own finite input".into());}
        crate::gray_script::Plan::idle(seconds)?
    }else{match round["input_kind"].as_str() {
        Some("idle")=>crate::gray_script::Plan::control(None,seconds)?,
        Some("finite")=>crate::gray_script::Plan::control(Some(&round["input_plan"]["steps"]),seconds)?,
        _=>return Err("missing or unsupported control input kind".into()),
    }};
    if round["input_plan"]!=plan.to_value() || round["workload"]!=format!("workload-{label}.sh") || round["qualify"]!=false
        || !matches!(round["deployment"].as_str(),Some("recorded"|"ready")) {return Err("control round plan/path differs from its normalized plan".into());}
    Ok(plan)
}
fn validate_control_rounds(state:&Value)->Result<(),String> {
    if !state["input_plan"].is_null() || !state["fan"].is_null() {return Err("control session cannot own fixed input or fan conditioning".into());}
    match control_power_policy(state)? {
        "observe" if !state["charging"].is_null()=>return Err("observe control session cannot own charging".into()),
        "battery" if !state["charging"].is_object()=>return Err("battery control session is missing its charging obligation".into()),
        _=>{},
    }
    let mut labels=std::collections::HashSet::new();
    for round in state["rounds"].as_array().ok_or("missing control rounds")? {
        let label=round["label"].as_str().filter(|s|valid_round(s)).ok_or("invalid control round")?;
        if !labels.insert(label) {return Err("duplicate control round".into());}
        round_plan(state,label)?;
    }
    Ok(())
}
fn script_plan(state:&Value)->Result<crate::gray_script::Plan,String> {
    if !scripted(state) {return Err("finite input requires script parent".into());}
    let value=&state["input_plan"];
    let plan=crate::gray_script::parse(&value["steps"],value["seconds"].as_u64().ok_or("missing input seconds")?)?;
    if plan.to_value()!=*value {return Err("invalid normalized input plan".into());}
    Ok(plan)
}
fn time_window(value:&Value)->Result<(f64,f64),String> {
    let start=value["start"].as_f64().filter(|n|n.is_finite()&&*n>=0.0).ok_or("missing actual window start")?;
    let end=value["end"].as_f64().filter(|n|n.is_finite()&&*n>=start).ok_or("missing actual window end")?;
    Ok((start,end))
}
fn validate_input_window(evidence:&Value)->Result<(),String> {
    let receipt=&evidence["natural_completion"];
    if receipt["schema"]!="phonefarm/gray-input-complete@1" {return Err("input receipt schema differs".into());}
    for child in ["workload","trace","sampler"] {
        let key=format!("{child}-time.json");let value=&evidence["actual"][&key];
        let exit_key=format!("{child}_exit.txt");
        if value["schema"]!="phonefarm/gray-child-time@1" || value["child"]!=child
            || value["exit_code"].as_i64().map(|c|c.to_string()).as_deref()!=evidence["actual"][&exit_key].as_str() {
            return Err(format!("{child} actual time identity/exit differs"));
        }
        time_window(value)?;
    }
    let (start,end)=time_window(receipt)?;
    let input=time_window(&evidence["actual"]["workload-time.json"])?;
    let trace=time_window(&evidence["actual"]["trace-time.json"])?;
    if input!=(start,end) || trace.0<start || trace.1>end || evidence["actual"]["workload_exit.txt"]!="0" {
        return Err("actual trace window is not enclosed by successful finite input".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parent_identity_preserves_old_launch_but_does_not_mix_script_ownership() {
        let old=json!({"schema":SCHEMA,"identity":{}});
        let launch=json!({"completion_owner":"gray_eval_launch"});
        let script=json!({"completion_owner":"gray_eval_script"});
        assert!(validate_parent_kind(&old,&launch).is_ok());
        assert!(validate_parent_kind(&old,&script).is_err());
        let new=json!({"schema":SCRIPT_SCHEMA,"identity":script});
        assert!(validate_parent_kind(&new,&script).is_ok());
        assert!(validate_parent_kind(&new,&launch).is_err());
        let loadop=json!({"completion_owner":"gray_ab_loadop"});
        assert!(validate_parent_kind(&json!({"schema":LOADOP_SCHEMA,"identity":loadop}),&loadop).is_ok());
        assert!(validate_parent_kind(&new,&loadop).is_err());
        let control=json!({"completion_owner":"gray_layer_control","control_parent_schema":CONTROL_SCHEMA});
        let mut current=json!({"schema":CONTROL_SCHEMA,"identity":control,"power_policy":"observe","input_plan":null,"fan":null,"charging":null,"rounds":[]});
        assert!(validate_parent_kind(&current,&control).is_ok());
        assert!(validate_parent_kind(&new,&control).is_err());
        for (label,seconds) in [("sample_1",1),("sample_2",120)] {
            let plan=crate::gray_script::Plan::idle(seconds).unwrap();
            assert_eq!(plan.duration_ms(),(seconds+10)*1000);
            assert!(!plan.shell().contains("input tap"));assert!(!plan.shell().contains("input swipe"));
            assert_eq!(crate::gray_script::parse(&plan.to_value()["steps"],seconds).unwrap(),plan);
            current["rounds"].as_array_mut().unwrap().push(json!({"label":label,"seconds":seconds,"input_plan":plan.to_value(),"input_kind":"idle","workload":format!("workload-{label}.sh"),"qualify":false,"deployment":"ready"}));
        }
        assert!(validate_control_rounds(&current).is_ok());
        assert_eq!(round_plan(&current,"sample_1").unwrap().seconds,1);
        let mut legacy=current.clone();legacy["schema"]=json!(LEGACY_CONTROL_SCHEMA);
        legacy.as_object_mut().unwrap().remove("power_policy");
        legacy["identity"].as_object_mut().unwrap().remove("control_parent_schema");
        for round in legacy["rounds"].as_array_mut().unwrap() {round.as_object_mut().unwrap().remove("input_kind");}
        let legacy_id=json!({"completion_owner":"gray_layer_control","control_parent_schema":LEGACY_CONTROL_SCHEMA});
        assert!(validate_parent_kind(&legacy,&legacy_id).is_ok());
        assert!(validate_parent_kind(&legacy,&control).is_err());
        assert!(validate_parent_kind(&current,&legacy_id).is_err());
        assert!(validate_control_rounds(&legacy).is_ok());
        let finite=crate::gray_script::Plan::control(Some(&json!([{"action":"tap","x":2,"y":3},{"action":"sleep","ms":11000}])),1).unwrap();
        current["rounds"][0]["input_kind"]=json!("finite");current["rounds"][0]["input_plan"]=finite.to_value();
        assert!(validate_control_rounds(&current).is_ok());
        assert_eq!(round_plan(&current,"sample_1").unwrap(),finite);
        let mut input_parent=current.clone();input_parent["schema"]=json!(INPUT_CONTROL_SCHEMA);
        input_parent["identity"]["control_parent_schema"]=json!(INPUT_CONTROL_SCHEMA);
        input_parent.as_object_mut().unwrap().remove("power_policy");
        assert!(validate_parent_kind(&input_parent,&input_parent["identity"]).is_ok());
        assert!(validate_control_rounds(&input_parent).is_ok());
        assert_eq!(control_power_snapshot(&input_parent).unwrap()["policy"],"observe");
        input_parent["power_policy"]=json!("battery");
        assert!(validate_control_rounds(&input_parent).is_err());
        let mut battery=current.clone();battery["power_policy"]=json!("battery");
        assert!(validate_control_rounds(&battery).is_err());
        battery["charging"]=json!({"stage":"suspended","history":[],"record_path":"SECRET_PATH","binding":{"token":"SECRET_TOKEN"},
            "initial":{"status":"Charging","current_now_ua":100,"capacity_pct":80},
            "last_observation":{"stage":"suspended","control_modified_by_session":true,"on_battery":true,"observed_at":"2026-10-23T00:00:00Z",
                "control":{"node":"SECRET_NODE"},"record_path":"SECRET_PATH","battery":{"status":"Discharging","current_now_ua":-1000000,"capacity_pct":80,"on_battery":true}}});
        assert!(validate_control_rounds(&battery).is_ok());
        let public=control_power_snapshot(&battery).unwrap();
        assert_eq!(public["control_modified_by_session"],true);
        assert_eq!(public["last_observation"]["battery"]["current_ua"],-1000000);
        assert!(!public.to_string().contains("SECRET"));
        battery["charging"]["stage"]=json!("restoring");
        assert_eq!(control_power_snapshot(&battery).unwrap()["stage"],"restoring");
        assert!(control_power_snapshot(&battery).unwrap()["control_modified_by_session"].is_null());
        battery["charging"]["stage"]=json!("restored");battery["charging"]["history"]=json!([{"token":"SECRET_TOKEN"}]);
        assert_eq!(control_power_snapshot(&battery).unwrap()["control_modified_by_session"],true);
        battery["power_policy"]=json!("observe");assert!(validate_control_rounds(&battery).is_err());
        battery["power_policy"]=Value::Null;assert!(control_power_snapshot(&battery).is_err());
        legacy["rounds"][0]=current["rounds"][0].clone();
        assert!(validate_control_rounds(&legacy).is_err());
        current["rounds"][0]["input_plan"]=current["rounds"][1]["input_plan"].clone();
        assert!(validate_control_rounds(&current).is_err());
        let window=|child,start,end|json!({"schema":"phonefarm/gray-child-time@1","child":child,"exit_code":0,"start":start,"end":end});
        let mut evidence=json!({"natural_completion":{"schema":"phonefarm/gray-input-complete@1","start":100.0,"end":112.0},"actual":{
            "workload-time.json":window("workload",100.0,112.0),"trace-time.json":window("trace",106.0,109.0),"sampler-time.json":window("sampler",99.0,114.0),
            "workload_exit.txt":"0","trace_exit.txt":"0","sampler_exit.txt":"0"}});
        assert!(validate_input_window(&evidence).is_ok());
        evidence["actual"]["trace-time.json"]["end"]=json!(112.1);
        assert!(validate_input_window(&evidence).is_err());
    }
}
