//! Native refbench whitebox adapter. No layer injection, KGSL dependency or
//! fabricated display FPS: timing is raw Vulkan queue work from the test APK.
use crate::{confirmation_transport::new_file, devlease::Lease, gray_session::Session};
use serde_json::{json, Value};
use std::{path::Path, time::{Duration, Instant}};

const PKG:&str="io.github.hgamey.refbench";

pub fn handles(req:&Value)->bool {req["kind"]=="whitebox" && req["workload"]["carrier"]=="refbench"}

pub fn validate(req:&Value)->Result<(),String>{
    if !handles(req) || req["package"]!=PKG {return Err("native whitebox requires refbench package/carrier".into());}
    if req["payload"]["knobs"]!=json!({"loadop":"on"}) {return Err("refbench whitebox only accepts knobs={loadop:on}; baseline is off".into());}
    for key in req["payload"].as_object().ok_or("missing payload")?.keys(){
        if !matches!(key.as_str(),"knobs"|"quality_frames") {return Err(format!("unsupported native whitebox payload.{key}"));}
    }
    for key in req["workload"].as_object().ok_or("missing workload")?.keys(){
        if !matches!(key.as_str(),"carrier"|"frames"|"intensity") {return Err(format!("unsupported native whitebox workload.{key}"));}
    }
    for key in req["conditions"].as_object().ok_or("missing conditions")?.keys(){
        if !matches!(key.as_str(),"require_root"|"allow_missing_power"|"cool_c"|"temp_cap_c") {return Err(format!("unsupported native whitebox conditions.{key}"));}
    }
    if req["conditions"]["require_root"]!=true || req["conditions"]["allow_missing_power"]!=true {
        return Err("native whitebox requires root and explicit allow_missing_power=true".into());
    }
    if req["protocol"]["alternating"]!=true {return Err("native whitebox requires alternating A/B".into());}
    let rounds=req["protocol"]["rounds"].as_u64().ok_or("missing rounds")?;
    if !(1..=64).contains(&rounds) {return Err("rounds must be1..64".into());}
    let frames=req["workload"]["frames"].as_u64().ok_or("missing frames")?;
    let intensity=req["workload"]["intensity"].as_u64().ok_or("missing intensity")?;
    let quality=match req["payload"].get("quality_frames"){None=>60,Some(v)=>v.as_u64().ok_or("quality_frames must be an integer")?};
    if !(60..=100000).contains(&frames) || !(1..=64).contains(&intensity) || !(60..=100000).contains(&quality) {
        return Err("invalid native whitebox frame/intensity count".into());
    }
    let cool=req["conditions"]["cool_c"].as_f64().ok_or("missing cooling threshold")?;
    let cap=req["conditions"]["temp_cap_c"].as_f64().ok_or("missing temperature cap")?;
    if !(20.0..=60.0).contains(&cool) || !(cool..=75.0).contains(&cap){return Err("invalid native whitebox temperature limits".into());}
    Ok(())
}

fn save(path:&Path,value:&Value)->Result<(),String>{
    new_file(path,&serde_json::to_vec_pretty(value).map_err(|e|e.to_string())?)
}

fn facts(app:&Value,label:&str,on:bool,frames:u32,intensity:u32,quality:bool)->Result<(),String>{
    let expected=if on{"DONT_CARE"}else{"LOAD"};
    if app["contract_version"]!=1 || app["scene"]!="bw_pingpong" || app["run_id"]!=label
        || app["params"]["frames"]!=frames || app["params"]["intensity"]!=intensity
        || app["frames_submitted"]!=frames || app["clean_exit"]!=true
        || app["effective_knobs"]["loadop"]!=if on{"on"}else{"off"}{return Err("whitebox application facts mismatch".into());}
    let passes=app["passes"].as_array().ok_or("missing pass declarations")?;
    let ping:Vec<_>=passes.iter().filter(|p|p["name"].as_str().is_some_and(|s|s.starts_with("ping_"))).collect();
    if ping.len()!=intensity as usize || ping.iter().any(|p|p["load_op"]!=expected){return Err("native loadOp readback mismatch".into());}
    if quality && (app["capture_mode"]!="quality_only" || app["performance_eligible"]!=false){return Err("quality capture not excluded from performance".into());}
    if !quality && (app["performance_eligible"]==false || app["capture_mode"]=="quality_only"){return Err("quality-only run cannot be timed".into());}
    Ok(())
}

fn json_output(session:&Session,name:&str,out:&Path)->Result<Value,String>{
    let raw=session.whitebox_refbench_output(name)?.ok_or_else(||format!("missing {name}"))?;
    new_file(&out.join(name),&raw.bytes)?;
    serde_json::from_slice(&raw.bytes).map_err(|e|format!("invalid {name}: {e}"))
}

fn arm(session:&Session,index:u64,frames:u32,intensity:u32,quality:bool,out:&Path)->Result<Value,String>{
    std::fs::create_dir(out).map_err(|e|e.to_string())?;
    let label=session.start_whitebox_refbench_arm(index,frames,intensity,quality)?;
    let start=Instant::now();
    while session.target_alive()? {
        if start.elapsed()>Duration::from_secs(90){return Err("whitebox arm did not exit within90s".into());}
        std::thread::sleep(Duration::from_millis(250));
    }
    let started=session.whitebox_refbench_output("refbench_started")?.ok_or("missing started marker")?;
    if std::str::from_utf8(&started.bytes).map_err(|e|e.to_string())?.trim()!=format!("bw_pingpong {label}") {
        return Err("started marker binding mismatch".into());
    }
    new_file(&out.join("started.txt"),&started.bytes)?;
    let app=json_output(session,"refbench_out.json",out)?;
    facts(&app,&label,index%2==1,frames,intensity,quality)?;
    let mut result=json!({"index":index,"run_id":label,"arm":if index%2==0{"A"}else{"B"},
        "quality_only":quality,"frames":frames,"intensity":intensity,"application_facts_verified":true,"natural_exit":true});
    if quality {
        let hash=json_output(session,"refbench_bw_hash.json",out)?;
        if hash["schema"]!="refbench/bw-output-hash@1" || hash["run_id"]!=label || hash["complete"]!=true
            || hash["frames"]!=frames || hash["width"]!=2048 || hash["height"]!=2048
            || hash["bytes"]!=33554432u64 || hash["format"]!="R16G16B16A16_SFLOAT"
            || hash["sha256"].as_str().is_none_or(|s|s.len()!=64 || !s.bytes().all(|b|b.is_ascii_hexdigit())) {
            return Err("invalid complete offscreen GPU readback hash".into());
        }
        result["output_hash"]=hash;
    }else{
        let raw=json_output(session,"refbench_gpu_timestamps.json",out)?;
        let samples=crate::refbench_timing::parse(&raw.to_string(),&label,frames)?;
        result["gpu_frame_elapsed_mean_ms"]=json!(crate::gpustat::mean(&samples.gpu_elapsed_ms));
        result["gpu_frame_elapsed_p95_ms"]=json!(crate::gpustat::percentile(&samples.gpu_elapsed_ms,0.95));
        result["gpu_queue_interval_p95_ms"]=json!(crate::gpustat::percentile(&samples.intervals_ms,0.95));
        result["samples_after_warmup"]=json!(samples.gpu_elapsed_ms.len());
    }
    save(&out.join("arm.json"),&result)?;
    Ok(result)
}

pub fn run(req:&Value,lease:Lease,out:&Path)->Result<Value,String>{
    validate(req)?;
    std::fs::create_dir_all(out).map_err(|e|e.to_string())?;
    save(&out.join("frozen-request.json"),req)?;
    let serial=lease.serial.clone();
    let phone=crate::device::Device::new(Some(serial.clone()),out.to_string_lossy().into());
    if !crate::hwcond::root_ok(&phone){return Err("native whitebox requires working root".into());}
    let session=Session::new_whitebox_refbench(lease,out)?;
    let mut quality=Vec::new();let mut timed=Vec::new();
    let frames=req["workload"]["frames"].as_u64().unwrap() as u32;
    let intensity=req["workload"]["intensity"].as_u64().unwrap() as u32;
    let rounds=req["protocol"]["rounds"].as_u64().unwrap();
    let collect=(||->Result<(),String>{
        session.prepare()?;
        for index in 0..2 {
            quality.push(arm(&session,index,req["payload"]["quality_frames"].as_u64().unwrap_or(60) as u32,
                intensity,true,&out.join(format!("quality_{index}")))?);
        }
        if quality[0]["output_hash"]["sha256"]!=quality[1]["output_hash"]["sha256"]{return Err("native whitebox output hashes differ".into());}
        for index in 2..2+rounds*2 {
            let (zone,c,waited)=crate::hwcond::wait_cool("whitebox-refbench",&phone,req["conditions"]["cool_c"].as_f64().unwrap(),90)?;
            let mut value=arm(&session,index,frames,intensity,false,&out.join(format!("timed_{index:02}")))?;
            let after=crate::hwcond::soc_max_c(&crate::hwcond::read_thermal(&phone)).ok_or("missing post-arm temperature")?;
            if after.1>req["conditions"]["temp_cap_c"].as_f64().unwrap(){return Err("post-arm temperature guard reached".into());}
            value["start_temp_c"]=json!(c);value["end_temp_c"]=json!(after.1);value["thermal_zone"]=json!(zone);value["waited_s"]=json!(waited);
            eprintln!("[whitebox] {}/{} {} GPU p95 {:.3}ms",index-1,rounds*2,value["arm"],value["gpu_frame_elapsed_p95_ms"].as_f64().unwrap());
            timed.push(value);
        }
        Ok(())
    })();
    let recovery=session.finish_whitebox_refbench(collect.as_ref().err().map(String::as_str))
        .unwrap_or_else(|e|json!({"status":"required","error":e}));
    let mut report=json!({"version":2,"kind":"whitebox","adapter":"refbench_native_loadop_v1","candidate_id":req["candidate_id"],
        "status":if collect.is_ok() && recovery["status"]=="reported_complete"{"PASS"}else{"ABORT"},
        "session":session.dir,"serial":serial,"source":"phonefarm","error":collect.err(),"recovery":recovery,
        "quality_runs":quality,"timed_runs":timed,"conditions_requested":req["conditions"],
        "metrics":{"operator_latency_ms":null,"fps_p95_ms":null,"psnr_db":null,"power_watt":null},
        "measurement_scope":"reference_app_native_gpu_frame_only","metric_provenance":{"native_metrics":"Vulkan queue start/end queries; not display FPS"},
        "qualification":{"can_claim_game_improvement":false,"whole_device_power_verified":false,"frequency_locked":false,"full_window_thermal_verified":false},
        "verdict":{"is_pareto_improvement":false},"unavailable":["display FPS not captured","whole-device power unavailable by explicit opt-in","PSNR not computed: separate quality runs compare full FP16 GPU readback SHA256"]});
    if report["status"]=="PASS" {
        let series=|label:&str|timed.iter().filter(|v|v["arm"]==label).map(|v|v["gpu_frame_elapsed_p95_ms"].as_f64().unwrap()).collect::<Vec<_>>();
        let a=series("A");let b=series("B");let ma=crate::gpustat::mean(&a);let mb=crate::gpustat::mean(&b);
        let p=crate::gpustat::welch_t_test(&a,&b).map(|v|v.p_value);
        report["native_metrics"]=json!({"baseline_gpu_frame_p95_ms":ma,"candidate_gpu_frame_p95_ms":mb,"relative_reduction":1.0-mb/ma});
        report["image_check"]=json!({"method":"full_offscreen_rgba16f_gpu_readback_sha256","equal":true,"bytes_per_arm":33554432,"separate_from_timing":true});
        report["verdict"]["p_value"]=json!(p);
        report["verdict"]["is_reference_gpu_p95_improvement"]=json!(req["protocol"]["stage"]!="screen" && 1.0-mb/ma>=0.05 && p.is_some_and(|v|v<req["protocol"]["p_threshold"].as_f64().unwrap()));
    }
    save(&out.join("whitebox-report.json"),&report)?;
    Ok(report)
}

pub fn recover(args:&[String])->i32{
    let value=(||->Result<Value,String>{
        let mut serial=None;let mut dir=None;let mut it=args.iter();
        while let Some(flag)=it.next(){match flag.as_str(){"--serial"=>serial=it.next().cloned(),"--session"=>dir=it.next().cloned(),"--json"=>{},_=>return Err("usage: whitebox-refbench-recover --serial S --session DIR --json".into())}}
        Session::resume_whitebox_refbench(Path::new(&dir.ok_or("missing session")?),&serial.ok_or("missing serial")?)?.finish_whitebox_refbench(Some("explicit recovery; original experiment unchanged"))
    })();
    match value{Ok(v)=>{let code=if v["status"]=="reported_complete"{0}else{2};println!("{v}");code},Err(e)=>{eprintln!("{e}");2}}
}

#[cfg(test)] mod tests{
    use super::*;
    fn request()->Value{json!({"kind":"whitebox","package":PKG,"workload":{"carrier":"refbench","frames":60,"intensity":2},"payload":{"knobs":{"loadop":"on"}},"conditions":{"require_root":true,"allow_missing_power":true,"cool_c":50,"temp_cap_c":70},"protocol":{"rounds":8,"alternating":true}})}
    #[test]fn native_contract_rejects_dropped_conditions_and_wrong_targets(){
        assert!(validate(&request()).is_ok());
        let mut wrong=request();wrong["payload"]["quality_frames"]=json!("bad");assert!(validate(&wrong).is_err());
        for (field,value) in [("conditions",json!({"require_root":true,"allow_missing_power":true,"cool_c":50,"temp_cap_c":70,"gpu_level":43})),("payload",json!({"knobs":{"msaa":"1"}})),("package",json!("com.miHoYo.Yuanshen"))]{let mut v=request();v[field]=value;assert!(validate(&v).is_err());}
    }
    #[test]fn application_facts_require_real_native_switch_and_capture_scope(){
        let mut v=json!({"contract_version":1,"scene":"bw_pingpong","run_id":"wb_0_off","params":{"frames":60,"intensity":1},"frames_submitted":60,"clean_exit":true,"effective_knobs":{"loadop":"off"},"passes":[{"name":"ping_0","load_op":"LOAD"}],"capture_mode":"quality_only","performance_eligible":false});
        assert!(facts(&v,"wb_0_off",false,60,1,true).is_ok());
        assert!(facts(&v,"wb_0_off",false,60,1,false).is_err());
        v["passes"][0]["load_op"]=json!("DONT_CARE");assert!(facts(&v,"wb_0_off",false,60,1,true).is_err());
    }
}
