#!/usr/bin/env python3
"""Explicit simulation only. Implements transport receipt protocol, never real-device data."""
import base64, hashlib, json, os, pathlib, re, shlex, sys
root=pathlib.Path(os.environ['PHONEFARM_FAKE_DEVICE_DIR']); root.mkdir(parents=True,exist_ok=True)
if os.environ.get('PHONEFARM_FAKE_FAIL') == 'host_parent_killed':
    import signal,time
    signal.signal(signal.SIGTERM, lambda *_:(root/'transport-term').touch())
    signal.alarm(15)
    (root/'transport-group').write_text(str(os.getpgrp()))
    while True: time.sleep(.01)
statepath=root/'fake-state.json'
if statepath.exists(): state=json.loads(statepath.read_text())
else:
    state={'files':{},'runs':0}
    state['snapshot']='''# device_snapshot v1
ftrace.tracing_on=0
ftrace.current_tracer=nop
ftrace.buffer_size_kb=65536
ftrace.set_event=
kgsl.min_pwrlevel=5
kgsl.max_pwrlevel=0
kgsl.default_pwrlevel=0
kgsl.thermal_pwrlevel=0
kgsl.force_clk_on=0
kgsl.force_rail_on=0
kgsl.force_bus_on=0
kgsl.force_no_nap=0
kgsl.idle_timer=80
kgsl.max_gpuclk=1000
kgsl.pwrscale=1
kgsl.bus_split=1
kgsl.perfcounter=1
kgsl.devfreq_governor=msm-adreno-tz
kgsl.devfreq_min_freq=100
kgsl.devfreq_max_freq=1000
bus.DDR.boost_freq=0
bus.DDR.hw_max_freq=1000
bus.DDR.hw_min_freq=100
bus.LLCC.boost_freq=0
bus.LLCC.hw_max_freq=1000
bus.LLCC.hw_min_freq=100
wm.size=Physical size: 1080x2400
wm.density=Physical density: 480
settings.peak_refresh_rate=120
settings.min_refresh_rate=60
settings.refresh_rate_mode=0
cpu.policy0.governor=schedutil
cpu.policy0.scaling_max=1000
cpu.policy0.scaling_min=100
# end
'''
def thermal_hal():
    status='3' if fail=='thermal_severe' else '2'
    override='true' if fail=='thermal_override' else 'false'
    out=f'IsStatusOverride: {override}\nThermal Status: {status}\nHAL Ready: true\n'
    for ty,name,t in [(0,'CPU0',95),(1,'GPU0',95),(2,'battery',80),(3,'skin',60)]:
        out+=f'TemperatureThreshold{{mType={ty}, mName={name}, mHotThrottlingThresholds=[NaN, NaN, NaN, {t}.0, NaN, NaN, 115.0], mColdThrottlingThresholds=[]}}\n'
    return out
def thermal_raw(step=5,up=None):
    up=state.get('thermal_uptime',2000.) if up is None else up;state['thermal_uptime']=up+step
    call=state.get('thermal_calls',0);state['thermal_calls']=call+1
    out=f'#phonefarm_thermal_sample v1\nUPTIME\t{up}\n'
    for path,v in [('/sys/class/thermal/thermal_zone0/temp',80000),('/sys/class/thermal/thermal_zone1/temp',60000),('/sys/class/thermal/thermal_zone2/temp',50000),('/sys/class/power_supply/battery/temp',390)]:
        if 'thermal_zone0' in path and fail in ['capability_fluctuating','thermal_drift_31']:v+=((call%2)* (7700 if fail=='capability_fluctuating' else 3100))
        if fail=='thermal_missing' and 'battery' in path:continue
        if fail=='thermal_hot_before_probe' and state['files'] and 'thermal_zone0' in path:v=92000
        out+=f'TEMP\t{path}\t{v}\n'
    return out+'#hal_begin\n'+thermal_hal()+'#hal_end\n#end\n'
def render_raw(up, active=True):
    raw=(root/'render-origin.raw.tsv').read_text().replace('UPTIME_END_PLACEHOLDER',str(up+.1)).replace('UPTIME_PLACEHOLDER',str(up))
    if active:raw=raw.replace('[local] boot global','local [boot] global')
    if fail=='origin_loss':raw=raw.replace('overrun: 0','overrun: 1')
    if fail=='origin_epoch' and state['runs']>3:raw=raw.replace('320740','320741')
    return raw

def render_trace(trace,base):
    records=[]
    for line in trace.splitlines():
        m=re.match(r'\s*(.*?)-(\d+)\s+\[(\d+)\]\s+\S+\s+(\d+\.\d+):\s+(\w+):\s*(.*)',line)
        if not m:continue
        if m[5]=='adreno_cmdbatch_submitted':
            tid=32074 if m[1].strip()=='UnityGfxDeviceW' else 2222
            if fail=='origin_foreign':tid=2222
            kv=dict(re.findall(r'(\w+)=([^ ]+)',m[6]));t=float(m[4])
            records.append((t-.0002,f' UnityGfxDeviceW-{tid} [000] ..... {t-.0002:.6f}: adreno_cmdbatch_queued: ctx={kv["ctx"]} ctx_prio=0 ts={kv["ts"]} queued=2 flags=IB_LIST'))
            records.append((t,f' worker-555 [003] ..... {t:.6f}: adreno_cmdbatch_submitted: ctx={kv["ctx"]} ts={kv["ts"]} flags=IB_LIST'))
    if fail in ['origin_spf1','origin_spf3']:
        records=[];spf=int(fail[-1]);pattern=[15,15,16,17,18,18,17,16] if spf==1 else [2,5,26];t=base+1.15
        for n in range(140):
            if t>base+3.5:break
            records.extend([(t-.0002,f' game-32074 [000] ..... {t-.0002:.6f}: adreno_cmdbatch_queued: ctx=41 ctx_prio=0 ts={n+1} queued=2 flags=IB_LIST'),(t,f' worker-555 [003] ..... {t:.6f}: adreno_cmdbatch_submitted: ctx=41 ts={n+1} flags=IB_LIST')]);t+=pattern[n%len(pattern)]/1000
    if fail=='origin_tail_scope' and state['runs']>3:
        records.extend([(base+3.55,f' game-32000 [000] ..... {base+3.55:.6f}: adreno_cmdbatch_queued: ctx=99 ctx_prio=0 ts=1 queued=1 flags=IB_LIST'),(base+3.65,f' worker-555 [003] ..... {base+3.65:.6f}: adreno_cmdbatch_submitted: ctx=99 ts=1 flags=IB_LIST')])
    if fail=='origin_unmatched':records=[(t,l) for t,l in records if 'queued:' not in l]
    records.sort()
    return f'#loop_v1_meta capture_start_uptime={base+1.1} capture_end_uptime={base+3.56} duration_req=2 join_start_uptime={base-.3} tail_end_uptime={base+6.56} join_end_uptime={base+6.8}\n'+'\n'.join(l for _,l in records)+'\n'
args=sys.argv[1:];assert args[:2]==['-s','fake-target'] and args[2]=='shell'
cmd=shlex.split(args[3]);assert cmd[:2]==['su','-c']; cmd=cmd[2]; data=sys.stdin.buffer.read()
with (root/'argv.jsonl').open('a') as f:f.write(json.dumps({'argv':args,'command':cmd,'stdin':data.decode('latin1'),'simulation':True})+'\n')
fail=os.environ.get('PHONEFARM_FAKE_FAIL','')
if fail=='closed_pipes_child' and cmd=='sh' and b'# device_snapshot' in data:
    pid=os.fork()
    if pid==0:
        os.close(1);os.close(2)
        import time;time.sleep(20);os._exit(0)
    sys.exit(0)
if fail=='root' and cmd.startswith('set -eu; id;'):
    print('uid=2000(shell)');print('fake-serial');print('fake-boot');print('/system/bin/setsid');sys.exit(0)
if cmd.startswith('set -eu; id;'):
    print('uid=0(root) gid=0(root)');print('fake-serial');print('changed-boot' if fail=='boot' else 'fake-boot');print('/system/bin/setsid')
elif cmd=='dumpsys activity activities': print('mResumedActivity: ActivityRecord x '+('com.example.crashed/.Home' if fail=='foreground' else 'com.example.game/.Game')+' t1')
elif cmd=='timeout 8 dumpsys thermalservice':sys.stdout.write(thermal_hal())
elif cmd.startswith('timeout 20 setsid sh -s -- ') and b'# Receiver-owned finite helper-only dead retirement.' in data:
    d,token,boot,run,mode=shlex.split(cmd)[-5:]
    if fail=='native_files':sys.exit(73)
    if fail=='native_partial':print('#phonefarm_render_witness v2');sys.exit(0)
    def ps(p,g,t):return f'{p} (synthetic name with spaces) S 1 {g} 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 {t} 0\n'
    ap,np=(300,400) if mode!='remove' else (500,600)
    records=[('owner',token+' '+boot+'\n'),('master',f'{token} {boot} 100 9 render_initial\n'),('children',f'render_reader\t200\t8\t{d}/render_ownership.sh\n'),('boot_before',boot+'\n'),('boot_after',boot+'\n'),('actor',ps(ap,ap,7)),('native_child',ps(np,np,6)),('inventory_count','2'),(f'process_stat/{ap}',ps(ap,ap,7)),(f'process_stat/{np}',ps(np,np,6))]
    if fail=='native_live':records.append(('process_stat/100',ps(100,100,99)));records=[(n,'3' if n=='inventory_count' else b)for n,b in records]
    if fail=='native_epoch':records=[(n,ps(np,np,99) if n=='native_child' else b)for n,b in records]
    print('#phonefarm_render_witness v2')
    for n,b in records:print(f'BEGIN\t{n}\n{b}\nEND\t{n}')
    print(f'#end\nACTOR {ap} 7 {ap}\nCHILD {np} 6 {np}\nSTAGE {mode} {run}\n#phonefarm_native_helper_end@1')
    if mode=='close':state['files'][d+'/session.closed']=token+' '+boot+' '+run
    if mode=='remove':state['files']={k:v for k,v in state['files'].items() if not k.startswith(d+'/')}
elif cmd=='timeout 20 sh -s' and b'NATIVE_HELPER_POST_VERIFIED' in data:
    if fail=='native_post':sys.exit(77)
    print('NATIVE_HELPER_POST_VERIFIED')
elif cmd.startswith('timeout 20 sh -s -- ') and b'# Fixed host-supplied read-only diagnostic' in data:
    if fail=='diagnostic_boot':sys.exit(72)
    if fail=='diagnostic_partial':print('#phonefarm_helper_diagnostic@1');sys.exit(0)
    print('#phonefarm_helper_diagnostic@1')
    words=shlex.split(cmd);d,token,boot=words[-3:];owner=token+' '+boot+'\n'
    print('BEGIN\towner\t'+str(len(owner)));print(owner,end='');print();print('END\towner')
    for label in ['closed','lock_owner','master','children','pending','permit','reader_log','reader_stderr','reader_exit','quiescence','journal']:
        print('MISSING\t'+label)
    print('SELECTION\tmaster\tUNKNOWN');print('SELECTION\tchild\tUNKNOWN')
    print('SCAN\t0\t0');print('#phonefarm_helper_diagnostic_end@1')
elif cmd.startswith('sh -s -- ') and b'#phonefarm_render_witness v2' in data:
    sys.stdout.write(render_raw(1045975.,False))
elif cmd.startswith('sh -s -- ') and b'# Fixed helper-only closure checker' in data:
    words=shlex.split(cmd);d,token,boot,run,mode=words[3:]
    if fail=='helper_group_unknown':sys.exit(77)
    assert state['files'][d+'/session.closed']==token+' '+boot+' '+run
    print(('HELPER_CLOSED' if mode=='verify' else 'HELPER_REMOVED')+' '+token+' '+boot+' '+run)
    if mode=='remove':state['files']={k:v for k,v in state['files'].items() if not k.startswith(d+'/')}
elif cmd.startswith('set -eu; test ') and 'HELPER_ABSENT' in cmd:
    d=re.search(r"test ! -e '([^']+)'",cmd).group(1)
    assert not any(k.startswith(d+'/') for k in state['files'])
    print('HELPER_ABSENT')
elif cmd.startswith('set -eu; test ') and 'chmod 700' in cmd:
    assert any(k.endswith('/render_reader.elf') for k in state['files'])
elif cmd=='sh':
    if data.startswith(b'#!/system/bin/sh\n#phonefarm_fixed_tools_'):
        # Simulation is explicitly synthetic. Real template behavior is tested separately with host-owned files.
        manifest=json.loads(re.search(rb'^#MANIFEST (.+)$',data,re.M).group(1))
        d=manifest['remote'];owner=manifest['token']+' '+manifest['boot_id']
        digest=hashlib.sha256(json.dumps(manifest,ensure_ascii=False,indent=2,separators=(',', ': ')).encode()).hexdigest()
        # Rust pretty JSON and this fixture use the same stable typed field order, no trailing LF.
        if b'#phonefarm_fixed_tools_install v1' in data:
            assert d+'/owner' not in state['files']
            state['files'][d+'/owner']=owner+'\n'
            blocks=re.findall(rb"write_fixed '([^']+)' ([0-9]+) <<'PHONEFARM_FIXED_BUNDLE_DATA'\n(.*?)PHONEFARM_FIXED_BUNDLE_DATA\n",data,re.S)
            assert len(blocks)==manifest['count']
            for i,(name,size,raw) in enumerate(blocks):
                b=base64.b64decode(b''.join(raw.split()),validate=True);assert len(b)==int(size)
                state['files'][d+'/'+name.decode()]=b.decode('latin1')
                if fail=='batch_partial_install' and i==1:
                    statepath.write_text(json.dumps(state));sys.exit(91)
            if fail=='batch_corrupt_remote':state['files'][d+'/worker.sh']='X'+state['files'][d+'/worker.sh'][1:]
            print(f"INSTALL_COMPLETE\t{manifest['token']}\t{manifest['boot_id']}\t{digest}\t{manifest['count']}")
        else:
            if d+'/session.closed' in state['files']:sys.exit(86)
            if state['files'].get(d+'/owner')!=owner+'\n':sys.exit(87)
            print('#phonefarm_fixed_tools_verification v1')
            print(f'OWNER\t{owner}\nBOOT\t{manifest["boot_id"]}\nBUNDLE\t{digest}')
            count=0
            for entry in manifest['files']:
                value=state['files'].get(d+'/'+entry['name'])
                if value is None:sys.exit(92)
                b=value.encode('latin1');print(f'FILE\t{entry["name"]}\t{len(b)}\t{hashlib.sha256(b).hexdigest()}');count+=1
            if fail=='batch_truncated_verify':sys.exit(0)
            print(f'COUNT\t{count}\n#end')
    elif b'#phonefarm_thermal_metadata v1' in data:
        print('#phonefarm_thermal_metadata v1\nBOOT\tfake-boot')
        for i,ty,dom,t in [(0,'cpu-0','cpu',108000),(1,'gpuss-0','gpu',95000),(2,'skin-msm-therm','skin',60000)]:
            unit='deci_celsius' if fail=='thermal_wrong_unit' and dom=='cpu' else 'milli_celsius'
            print(f'SENSOR\t/sys/class/thermal/thermal_zone{i}/temp\t{ty}\t{dom}\t{unit}\nTRIP\t/sys/class/thermal/thermal_zone{i}/temp\tpassive\t{t}')
        print('SENSOR\t/sys/class/power_supply/battery/temp\tbattery\tbattery\tdeci_celsius\n#end')
    elif b'#phonefarm_thermal_sample v1' in data:
        count=data.count(b'sh -c') or 1
        if fail=='thermal_short_admission' and count>=6:count=5
        for n in range(count):sys.stdout.write(thermal_raw(5,1045977.+state['runs']*20 if count==1 else None))
        if fail=='loaded_cpu_drift' and count>=6:
            state['snapshot_before_warmup']=state['snapshot']
            state['snapshot']=state['snapshot'].replace('cpu.policy0.scaling_max=1000','cpu.policy0.scaling_max=600')
    elif b'# device_snapshot' in data:
        if b'ftrace.trace_clock=' in data and 'ftrace.trace_clock=' not in state['snapshot']:state['snapshot']=state['snapshot'].replace('# end','ftrace.trace_clock=[local] boot global\n# end')
        sys.stdout.write(state['snapshot'])
    elif b'probe_sysparam' in data:
        print('# probe_sysparam v1\ncpu.policies=0')
        max_freq='600' if 'cpu.policy0.scaling_max=600' in state['snapshot'] else '1000'
        print(f'cpu.policy0.avail_freqs=100 300 600 1000\ncpu.policy0.avail_governors=schedutil performance\ncpu.policy0.scaling_min_freq.cur=100\ncpu.policy0.scaling_max_freq.cur={max_freq}\ncpu.policy0.scaling_governor.cur=schedutil')
        print('gpu.num_pwrlevels=6\ngpu.min_pwrlevel.cur=5\ngpu.max_pwrlevel.cur=0\ngpu.devfreq_path=/sys/class/kgsl/kgsl-3d0/devfreq\ngpu.devfreq.avail_freqs=100 300 600 1000\ngpu.devfreq.avail_governors=msm-adreno-tz performance\ngpu.devfreq.min_freq.cur=100\ngpu.devfreq.max_freq.cur=1000\ngpu.devfreq.governor.cur=msm-adreno-tz')
        for bus in ['DDR','LLCC']:print(f'bus.{bus}.boost_freq.cur=0\nbus.{bus}.hw_min_freq=100\nbus.{bus}.hw_max_freq=1000\nbus.{bus}.avail_freqs=100 300 600 1000')
        print('setting.system.peak_refresh_rate.read_exit=0\nsetting.system.min_refresh_rate.read_exit=0\nsetting.system.refresh_rate_mode.read_exit=0\ndisplay.modes=fps=60,fps=120\nsetting.system.peak_refresh_rate.cur=120\nsetting.system.min_refresh_rate.cur=60\nsetting.system.refresh_rate_mode.cur=0\nsetting.system.all_refresh_rate=60 120\nsetting.system.refresh_rate_mode.base_fps=60\n# end')
    elif b'hot=0' in data:print('cpu-0 45000' if fail=='cold' else 'cpu-0 39000')
    else:raise Exception('unknown stdin script')
elif cmd.startswith('set -eu; umask 077; mkdir '):
    words=shlex.split(cmd); remote=words[5].rstrip(';'); owner=words[8]
    # Extract with actual token following printf format.
    token=re.search(r"printf '%s\\n' '([^']+)'",cmd).group(1)
    state['files'][remote+'/owner']=token+'\n'
elif cmd.startswith('set -eu; test ') and 'cat >' in cmd:
    dst=shlex.split(cmd.split('cat > ',1)[1].split(';',1)[0])[0];state['files'][dst]=data.decode('latin1')
elif cmd.startswith('cat '):
    path=shlex.split(cmd)[1]
    sys.stdout.buffer.write(state['files'][path].encode('latin1'))
elif cmd.startswith('setsid sh ') and '/worker.sh ' in cmd:
    words=shlex.split(cmd)[1:]; d,token,boot,mode,run=words[2:7];r=d+'/'+run
    assert state['files'][d+'/owner'].strip()==token+' '+boot
    if d+'/session.closed' in state['files']:sys.exit(86)
    if fail=='probe' and mode in ['probe','control_probe']:sys.exit(74)
    if fail=='workload' and mode=='sample':sys.exit(91)
    if mode=='render_readonly':
        if fail in ['helper_read_failure','helper_interrupt']:
            statepath.write_text(json.dumps(state));sys.exit(81)
        state['files'][r+'/render_reader.log']=render_raw(1045975.,False)
        state['files'][r+'/render_exit.txt']='0\n'
        state['files'][r+'/quiescence.txt']='OWNED_GROUPS_ABSENT\n'
        print('READONLY_COMPLETE')
    elif mode=='reconcile_render':
        state['files'][d+'/session.closed']=token+' '+boot+' '+run
        print('READONLY_RECONCILE_COMPLETE')
    elif mode in ['probe','control_probe']:
        names=['snapshot_before','snapshot_at_run','snapshot_after_run','snapshot_restored'] if mode=='control_probe' else ['snapshot_before','snapshot_restored']
        for name in names:
            snap=state['snapshot']
            if fail=='restore' and name=='snapshot_restored':snap=snap.replace('kgsl.force_clk_on=0','kgsl.force_clk_on=1')
            state['files'][r+'/'+name+'.txt']=snap;state['files'][r+'/'+name+'.exit']='0\n'
        key,kind,path,val=state['files'][d+'/'+run+'.plan.tsv'].strip().split('\t')
        receipt=f'{key}.writable=yes\n{key}.effect=live\n{key}.effect_restored=yes\n'
        if key=='setting.system.refresh_rate_mode':receipt+=f'{key}.mode{val}_fps=120 (readback={val})\n'
        state['files'][r+'/probe_receipt.txt']=receipt
        if mode=='control_probe' and d+'/thermal_sample.sh' in state['files']:
            if d+'/thermal-purpose.txt' in state['files']:
                start=state.get('thermal_uptime',2000.)
                series=''.join(thermal_raw(1,start+offset) for offset in [0,1.1,6.1,11.1])
                if fail=='control_hold_hot':series=series.replace('\t80000\n','\t92000\n')
                if fail=='control_hold_severe':series=series.replace('Thermal Status: 2','Thermal Status: 3')
                series+=f'#phonefarm_control_hold_complete start={start+.05} end={start+11.15} samples=4 profile=capability_absolute_v1\n'
                if fail=='control_hold_incomplete':series=series.split('#phonefarm_control_hold_complete')[0]
                state['files'][r+'/thermal_hold.raw.tsv']=series
            else:state['files'][r+'/thermal_hold.raw.tsv']=''.join(thermal_raw(5) for _ in range(4))
        print('PROBE_COMPLETE')
    elif mode=='sample':
        duration,lead,envdur=int(words[7]),int(words[8]),int(words[9]);role=words[10]
        base=1045978.0+state['runs']*20;state['runs']+=1
        trace=(root/'trace_fixture.txt').read_text().split('\n',1)[1]
        trace=re.sub(r'10459\d{2}\.\d+',lambda m:f'{float(m[0])+(base-1045978):.6f}',trace)
        state['files'][r+'/trace.txt']=f'#loop_v1_meta capture_start_uptime={base+1.1} capture_end_uptime={base+3.56} duration_req=2\n'+trace
        if d+'/render_ownership.sh' in state['files']:
            state['files'][r+'/trace.txt']=render_trace(trace,base)
            state['files'][r+'/render-before.raw.tsv']=render_raw(base-.2)
            after=render_raw(base+6.9)
            for cpu in [0,3]:
                n=sum(f'[{cpu:03}]' in line for line in state['files'][r+'/trace.txt'].splitlines());after=after.replace(f'BEGIN\tstats/cpu{cpu}\nentries: 0',f'BEGIN\tstats/cpu{cpu}\nentries: {n}')
            state['files'][r+'/render-after.raw.tsv']=render_raw(base+6.6)
            state['files'][r+'/render-final.raw.tsv']=after
        env=f'#sample_env v1 dur={envdur} interval=1\n#battery_status=Discharging\n#battery_capacity=80\n#fan_state=none\n# fixture-run={run}\n'
        for n in range(envdur):env+=f'ENV {base+n} 3850000 -1000000 NA 0 0 cpu-0 39000\n'
        env+='#battery_status_after=Discharging\n#sample_env done\n'
        if fail=='power':env=env.replace('Discharging','Charging')
        if fail=='env':env=env.replace('#sample_env done\n','')
        state['files'][r+'/sampler.log']=env
        if d+'/thermal_sampler.sh' in state['files']:
            start=base-.5;dur=envdur+3
            series=''.join(thermal_raw(1,start+n) for n in range(dur+1))
            series+=f'#phonefarm_thermal_window_complete dur={dur} interval=1 start={start} end={start+dur+.05} samples={dur+1}\n'
            if fail=='thermal_window_short':series=series.replace('samples=', 'invalid_samples=')
            if fail=='thermal_window_hot':series=series.replace('\t80000\n','\t92000\n')
            state['files'][r+'/thermal_sampler.log']=series
            state['files'][r+'/thermal_sampler_exit.txt']='1\n' if fail=='thermal_sampler_exit' else '0\n'

        for name in ['workload_exit','sampler_exit','capture_exit','restore_exit']:state['files'][r+'/'+name+'.txt']='0\n'
        state['files'][r+'/root_id.txt']='uid=0(root) gid=0(root)\n';state['files'][r+'/boot_id.txt']=boot+'\n'
        active=state['snapshot']
        if role=='candidate':
            for line in state['files'][d+'/candidate.plan.tsv'].splitlines():
                key,kind,path,val=line.split('\t')
                if path.startswith('/sys/devices/system/cpu/cpufreq/'):
                    field={'scaling_governor':'governor','scaling_min_freq':'scaling_min','scaling_max_freq':'scaling_max'}[path.rsplit('/',1)[1]];key='cpu.policy0.'+field
                elif path.startswith('/sys/class/kgsl/kgsl-3d0/devfreq/'):key='kgsl.devfreq_'+path.rsplit('/',1)[1]
                elif path.startswith('/sys/class/kgsl/'):key='kgsl.'+path.rsplit('/',1)[1]
                elif kind=='setting':key='settings.'+path.split(':')[1]
                active=re.sub(re.escape(key)+r'=.*',key+'='+val,active)
        for name,snap in [('snapshot_before',state['snapshot']),('snapshot_at_run',active),('snapshot_after_run',active),('snapshot_restored',state['snapshot'])]:
            if fail=='restore' and name=='snapshot_restored':snap=snap.replace('bus.DDR.boost_freq=0','bus.DDR.boost_freq=300')
            if fail=='active' and name=='snapshot_after_run':snap=snap.replace('kgsl.force_clk_on=0','kgsl.force_clk_on=1')
            state['files'][r+'/'+name+'.txt']=snap;state['files'][r+'/'+name+'.exit']='0\n'
        state['files'][r+'/quiescence.txt']='OWNED_GROUPS_ABSENT\n';print('SAMPLE_COMPLETE')
    elif mode in ['reconcile','reconcile_controls']:
        state['files'][r+'/snapshot_restored.txt']=state['snapshot'];state['files'][r+'/snapshot_restored.exit']='0\n';state['files'][r+'/restore_exit.txt']='0\n';state['files'][r+'/quiescence.txt']='OWNED_GROUPS_ABSENT\n';print('RECONCILE_COMPLETE')
        state['files'][d+'/session.closed']=token+' '+boot+' '+run
        if fail=='closure_unknown':
            statepath.write_text(json.dumps(state));sys.exit(87)
        state['files'][r+'/session_closed.txt']=f'SESSION_REVOKED {token} {boot} {run}\n'
    else:raise Exception('unknown worker mode')
else: raise Exception('unknown command '+cmd)
statepath.write_text(json.dumps(state))
