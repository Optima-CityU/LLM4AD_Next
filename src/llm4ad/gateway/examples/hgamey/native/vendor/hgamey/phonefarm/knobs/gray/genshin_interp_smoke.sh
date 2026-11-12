#!/bin/bash
# genshin_interp_smoke.sh — 层内多 present (interp, layer/interp.cpp) 在红魔 + 国服原神上的冒烟:
#   A 臂: 挂层, debug.knobs.interp=0 (层只计数)        → 显示帧间隔 = 游戏自己的节奏 (游戏内锁 30 帧时约 33.3 ms)
#   B 臂: 挂层, debug.knobs.interp=1 (缺省 mode=dup)    → 每帧之间多送一张, 显示帧间隔应减半 (约 16.7 ms)
# 看什么: B 的显示帧间隔 p50 / A 的 p50 是否落在 [0.4, 0.6] 之内 (只列数字, 不下通过/不通过), 且 B 的 marker 里 real ≈ synth、fallback 很少。
#
# 显示帧间隔的来源, 按顺序取第一个有数据的:
#   1. dumpsys SurfaceFlinger --latency '<原神图层>' 的 actual_present 列 (任务指定; 本机 Android 16 曾实测只回一行
#      vsync 周期、没有逐帧数据 —— 所以下面还有两条备用)
#   2. dumpsys SurfaceFlinger --timestats (-enable 后 -dump): 每图层 presentToPresent 直方图与 averageFPS
#   3. 层自己回读的 VkPastPresentationTimingGOOGLE (marker 的 interp.displayed_interval_ns, 只有 B 臂有);
#      A 臂则用 marker 的 readonly_stats.frames 增量 / 时间 算平均间隔
#
# 沿用 _tmp/npu/N1/ondevice.sh 的做法: 不动游戏的 lib 目录, 把「原目录副本 + 层」bind mount 盖上去, 还原 = umount;
# 手机上只新建目录和文件; 属性运行前记下、退出时逐个设回; 全局层属性在层挂上后立刻收回。
#
# 用法:
#   genshin_interp_smoke.sh                       跑 A、B 两臂 (ARMS 可改成 "B" 只跑 B)
#   genshin_interp_smoke.sh restore <手机上的 run 目录>   上一轮中途断掉时手动还原
# 环境变量:
#   SERIAL=91253241019A  RUN_S=40 (进大世界后每臂采多少秒)  GATE_WAIT=30 (层挂上后多久点「进门」)  GATE_X=1342 GATE_Y=651
#   ARMS="A B"  INTERP_MODE=dup  INTERP_PACING=1  INTERP_EXTRA=2  INTERP_ACQ_MS=4
#   PANEL60=0   1 = 把系统刷新率临时锁到 60 Hz (settings system peak/min_refresh_rate, 先记原值、退出时还原),
#                   给 interp_pacing=0 的纯 FIFO 双 present 用
#   LAYER_SO=<层 .so>  缺省 knobs/gray/build/out/libVkLayer_refknobs.so (先跑 build/build_layer.sh)
#   OUT=<Mac 上输出目录>  缺省 /Users/yao/Desktop/code/agent/hgamey/_tmp/ruler/I6/runs/<时间>
# 前提: 原神已登录 (脚本不登录)、游戏内帧率设 30 (A/B 都一样, 不在脚本里改)、启动后会停在「进门」画面。
set -uo pipefail

SERIAL="${SERIAL:-91253241019A}"
PKG=com.miHoYo.Yuanshen
HERE="$(cd "$(dirname "$0")" && pwd)"
LAYER_SO="${LAYER_SO:-$HERE/build/out/libVkLayer_refknobs.so}"
LAYER_DEV_NAME=libVkLayer_refknobs.so
RUN_S="${RUN_S:-40}"; GATE_WAIT="${GATE_WAIT:-30}"
GATE_X="${GATE_X:-1342}"; GATE_Y="${GATE_Y:-651}"
ARMS="${ARMS:-A B}"
INTERP_MODE="${INTERP_MODE:-dup}"; INTERP_PACING="${INTERP_PACING:-1}"
INTERP_EXTRA="${INTERP_EXTRA:-2}"; INTERP_ACQ_MS="${INTERP_ACQ_MS:-4}"
PANEL60="${PANEL60:-0}"
LAYER_NAME=VK_LAYER_refknobs_readonly
FILES_DIR=/storage/emulated/0/Android/data/$PKG/files
BASE=/data/local/tmp/interp_i6
PROPS="debug.vulkan.layers debug.knobs.loadop debug.knobs.passdump debug.knobs.copyprobe debug.knobs.upop
debug.knobs.frameaudit debug.knobs.frameaudit_ts debug.knobs.storeop debug.knobs.npuprobe debug.knobs.npuop
debug.knobs.framedump debug.knobs.framedump_go debug.knobs.interp debug.knobs.interp_mode debug.knobs.interp_pacing
debug.knobs.interp_extra_images debug.knobs.interp_acquire_ms debug.knobs.interp_csv"
SETTINGS60="peak_refresh_rate min_refresh_rate"

export PATH="$PATH:/opt/homebrew/bin"
ashell() { adb -s "$SERIAL" shell "$@" </dev/null | tr -d '\r'; }
asu() { adb -s "$SERIAL" shell "su -c '$1'" </dev/null | tr -d '\r'; }
asu_mm() { adb -s "$SERIAL" shell "su -mm -c '$1'" </dev/null | tr -d '\r'; }
die() { echo "错误: $*" >&2; exit 1; }

native_lib_dir() {
  local legacy
  legacy=$(ashell "dumpsys package $PKG" | grep -m1 legacyNativeLibraryDir | sed 's/.*legacyNativeLibraryDir=//')
  [ -n "$legacy" ] || return 1
  asu "test -d $legacy/arm64 && echo yes" | grep -q yes || return 1
  echo "$legacy/arm64"
}

# ── 还原: umount + 属性/设置回到运行前的值 ──
restore() {
  local run="$1" libdir
  libdir=$(asu "cat $run/state.libdir 2>/dev/null")
  echo "── 还原 ($run) ──"
  ashell "am force-stop $PKG"
  if [ -n "$libdir" ]; then
    for _ in $(seq 1 15); do [ -z "$(ashell "pidof $PKG")" ] && break; sleep 1; done
    for _ in 1 2 3 4 5 6; do
      asu_mm "grep -q \" $libdir \" /proc/mounts && echo m" | grep -q m || break
      if asu_mm "umount $libdir && echo ok" | grep -q ok; then echo "  已 umount $libdir"; else sleep 1; fi
    done
    asu_mm "grep -q \" $libdir \" /proc/mounts && echo m" | grep -q m \
      && echo "  警告: $libdir 仍是挂载点, 请手动 su -mm -c umount $libdir" >&2
    local now pre
    now=$(asu "ls $libdir" | sort | shasum | cut -c1-12)
    pre=$(asu "cat $run/pre_libdir_ls.txt" | sort | shasum | cut -c1-12)
    if [ "$now" = "$pre" ]; then echo "  lib 目录与运行前一致"
    else echo "  警告: lib 目录清单与运行前不同 (now=$now pre=$pre), 请人工核对 $run/pre_libdir_ls.txt" >&2; fi
  fi
  asu "cat $run/pre_props.txt 2>/dev/null" | while IFS='=' read -r k v; do [ -n "$k" ] && asu "setprop $k \"$v\""; done
  asu "cat $run/pre_settings.txt 2>/dev/null" | while IFS='=' read -r k v; do
    [ -n "$k" ] || continue
    if [ "$v" = "null" ] || [ -z "$v" ]; then ashell "settings delete system $k" >/dev/null; else ashell "settings put system $k $v"; fi
  done
  if asu "test -f $run/timestats_enabled && echo y" | grep -q y; then ashell "dumpsys SurfaceFlinger --timestats -disable" >/dev/null 2>&1; fi
  echo "  属性已还原: debug.vulkan.layers=[$(ashell 'getprop debug.vulkan.layers')] debug.knobs.interp=[$(ashell 'getprop debug.knobs.interp')]"
  echo "  留在手机上的文件 (不删): $run"
}

if [ "${1:-}" = "restore" ]; then
  [ -n "${2:-}" ] || die "用法: genshin_interp_smoke.sh restore /data/local/tmp/interp_i6/run_<时间>"
  restore "$2"; exit 0
fi

# ── 0. 本地与设备前置 ──
[ -f "$LAYER_SO" ] || die "缺层 $LAYER_SO (先跑 build/build_layer.sh)"
TS_NOW=$(date +%Y%m%d-%H%M%S)
OUT="${OUT:-/Users/yao/Desktop/code/agent/hgamey/_tmp/ruler/I6/runs/$TS_NOW}"
[ -e "$OUT" ] && die "$OUT 已存在, 不覆盖"
mkdir -p "$OUT"
RUN=$BASE/run_$TS_NOW
exec > >(tee "$OUT/smoke.log") 2>&1
echo "== I6 interp 冒烟 $TS_NOW  设备 $SERIAL  臂 [$ARMS]  mode=$INTERP_MODE pacing=$INTERP_PACING panel60=$PANEL60 =="
shasum "$LAYER_SO" | tee "$OUT/inputs.sha1"
adb -s "$SERIAL" get-state >/dev/null 2>&1 || die "adb 连不上 $SERIAL"
asu "id" | grep -q "uid=0" || die "没有 root"
LIBDIR=$(native_lib_dir) || die "找不到 $PKG 的 arm64 lib 目录"
asu_mm "grep -q \" $LIBDIR \" /proc/mounts && echo m" | grep -q m \
  && die "$LIBDIR 已经是挂载点 (上一轮没还原?), 先跑: $0 restore <上一轮 run 目录>"
asu "test -e $RUN && echo e" | grep -q e && die "$RUN 已存在"
LIB_LABEL=$(asu "ls -Zd $LIBDIR" | awk '{print $1}')
FILE_LABEL=$(asu "ls -Z $LIBDIR" | awk 'NF>=2{print $1; exit}')
LIB_KB=$(asu "du -sk $LIBDIR" | awk '{print $1}')
FREE_KB=$(asu "df -k /data" | awk 'NR==2{print $4}')
[ -n "$LIB_KB" ] && [ -n "$FREE_KB" ] && [ "$FREE_KB" -gt $((LIB_KB + 400000)) ] || die "/data 空间不够放 lib 目录副本"
echo "游戏 lib 目录 $LIBDIR (${LIB_KB} KB), /data 剩 ${FREE_KB} KB"
ashell "dumpsys display" | grep -iE "mActiveSfDisplayMode|refreshRate=|renderFrameRate" | head -5 > "$OUT/display_modes.txt"
echo "显示模式: $(head -c 300 "$OUT/display_modes.txt" | tr '\n' ' ')"

# ── 1. 推层 (只进新目录), 拼 lib 目录副本, bind mount ──
asu "mkdir -p $RUN/push $RUN/libdir && chmod 777 $RUN/push"
asu "ls $LIBDIR" > "$OUT/pre_libdir_ls.txt"
grep -qx "$LAYER_DEV_NAME" "$OUT/pre_libdir_ls.txt" && die "游戏 lib 目录里已有 $LAYER_DEV_NAME, 不覆盖, 停"
adb -s "$SERIAL" push "$OUT/pre_libdir_ls.txt" "$RUN/push/pre_libdir_ls.txt" >/dev/null
adb -s "$SERIAL" push "$LAYER_SO" "$RUN/push/$LAYER_DEV_NAME" >/dev/null || die "push 层失败"
asu "cp $RUN/push/pre_libdir_ls.txt $RUN/pre_libdir_ls.txt; echo $LIBDIR > $RUN/state.libdir"
asu "cp -a $LIBDIR/. $RUN/libdir/ && echo ok" | grep -q ok || die "复制 lib 目录失败"
N_ORIG=$(wc -l < "$OUT/pre_libdir_ls.txt" | tr -d ' '); N_COPY=$(asu "ls $RUN/libdir" | wc -l | tr -d ' ')
[ "$N_ORIG" = "$N_COPY" ] || die "lib 目录副本文件数 $N_COPY ≠ 原目录 $N_ORIG"
asu "cp $RUN/push/$LAYER_DEV_NAME $RUN/libdir/ && chown -R system:system $RUN/libdir && chmod 755 $RUN/libdir $RUN/libdir/* \
     && chcon $LIB_LABEL $RUN/libdir && chcon $FILE_LABEL $RUN/libdir/*" || die "属主/标签设置失败"
asu "echo \"$RUN: I6 interp 冒烟 $TS_NOW 用的 lib 目录副本 (原神 lib 目录 + 层), bind mount 用, 还原后留存未删\" >> $BASE/README.txt"

# ── 2. 记运行前的属性 / 设置, 挂 trap ──
: > "$OUT/pre_props.txt"
for k in $PROPS; do echo "$k=$(ashell "getprop $k")" >> "$OUT/pre_props.txt"; done
: > "$OUT/pre_settings.txt"
if [ "$PANEL60" = 1 ]; then for k in $SETTINGS60; do echo "$k=$(ashell "settings get system $k")" >> "$OUT/pre_settings.txt"; done; fi
adb -s "$SERIAL" push "$OUT/pre_props.txt" "$RUN/push/pre_props.txt" >/dev/null
adb -s "$SERIAL" push "$OUT/pre_settings.txt" "$RUN/push/pre_settings.txt" >/dev/null
asu "cp $RUN/push/pre_props.txt $RUN/pre_props.txt; cp $RUN/push/pre_settings.txt $RUN/pre_settings.txt"
trap 'restore "$RUN"' EXIT
if [ "$PANEL60" = 1 ]; then
  ashell "settings put system peak_refresh_rate 60"; ashell "settings put system min_refresh_rate 60"
  echo "已把刷新率锁到 60 Hz (退出时还原): $(ashell 'settings get system peak_refresh_rate')/$(ashell 'settings get system min_refresh_rate')"
fi
ashell "dumpsys SurfaceFlinger --timestats -enable" >/dev/null 2>&1 && asu "touch $RUN/timestats_enabled"
asu_mm "mount --bind $RUN/libdir $LIBDIR" || die "bind mount 失败"
asu "ls $LIBDIR" | grep -qx "$LAYER_DEV_NAME" || die "挂上后 lib 目录里看不到层"
echo "已 bind mount: $RUN/libdir -> $LIBDIR"
PRE_LAYERS=$(grep '^debug.vulkan.layers=' "$OUT/pre_props.txt" | cut -d= -f2-)

# ── 显示帧间隔的三条来源 ──
# 原神图层名 (BLAST 下可能有多个候选, 取 --latency 行数最多的那个)
genshin_layers() { ashell "dumpsys SurfaceFlinger --list" | grep -i "yuanshen" | grep -iv "background\|#[1-9]" ; }
sf_latency_sample() {   # $1 = 图层名 → 原样输出到 stdout
  adb -s "$SERIAL" shell "dumpsys SurfaceFlinger --latency '$1'" </dev/null | tr -d '\r'
}
# 把一段 --latency 采样 (多次拼接) 解析成 p50/p95: 去重 actual_present, 相邻差
latency_stats() {   # $1 = 文件
  python3 -I - "$1" <<'PY'
import sys
rows=set()
for ln in open(sys.argv[1]):
    p=ln.split()
    if len(p)!=3: continue
    try: a=int(p[1])
    except ValueError: continue
    if 0<a<(1<<62): rows.add(a)
v=sorted(rows)
d=[b-a for a,b in zip(v,v[1:]) if 1_000_000 < b-a < 1_000_000_000]
if len(d)<10: print("n=%d (不够)"%len(d)); sys.exit(0)
d.sort()
q=lambda p: d[min(len(d)-1,int(p*(len(d)-1)+0.5))]
print("n=%d p50_ms=%.2f p95_ms=%.2f max_ms=%.2f"%(len(d),q(.5)/1e6,q(.95)/1e6,d[-1]/1e6))
PY
}
timestats_stats() {   # $1 = timestats dump 文件 → 原神图层的 averageFPS 与 presentToPresent 直方图 p50
  python3 -I - "$1" <<'PY'
import sys,re
txt=open(sys.argv[1],errors="replace").read()
blocks=re.split(r"\n(?=layerName = )",txt)
for b in blocks:
    if "yuanshen" not in b.lower(): continue
    name=b.splitlines()[0]
    fps=re.search(r"averageFPS\s*=\s*([\d.]+)",b)
    m=re.search(r"presentToPresent histogram is as below:\s*\n(.*?)\n",b,re.S)
    hist=[]
    if m:
        for ms,c in re.findall(r"(\d+)ms=(\d+)",m.group(1)):
            hist+= [int(ms)]*int(c)
    hist.sort()
    p50 = hist[len(hist)//2] if hist else None
    print("%s averageFPS=%s presentToPresent_n=%d p50_ms=%s"%(name,fps.group(1) if fps else "?",len(hist),p50))
PY
}
marker_get() { asu "cat $FILES_DIR/knobs_layer_out.json 2>/dev/null"; }
marker_frames() { python3 -I -c 'import json,sys
try: print(json.load(sys.stdin)["readonly_stats"]["frames"])
except Exception: print(-1)'; }

# ── 一臂 ──
arm() {   # $1 = A|B
  local arm="$1" dir="$OUT/arm_$1" interp=0
  [ "$arm" = B ] && interp=1
  mkdir -p "$dir"
  echo "── 臂 $arm (debug.knobs.interp=$interp) ──"
  ashell "am force-stop $PKG"; sleep 2
  asu "setprop debug.knobs.loadop 0; setprop debug.knobs.passdump 0; setprop debug.knobs.copyprobe 0; setprop debug.knobs.upop 0; \
       setprop debug.knobs.frameaudit 0; setprop debug.knobs.storeop 0; setprop debug.knobs.npuprobe 0; setprop debug.knobs.npuop 0; \
       setprop debug.knobs.framedump 0; setprop debug.knobs.interp $interp; setprop debug.knobs.interp_mode $INTERP_MODE; \
       setprop debug.knobs.interp_pacing $INTERP_PACING; setprop debug.knobs.interp_extra_images $INTERP_EXTRA; \
       setprop debug.knobs.interp_acquire_ms $INTERP_ACQ_MS; setprop debug.knobs.interp_csv 1"
  [ "$(ashell 'getprop debug.knobs.interp')" = "$interp" ] || die "setprop debug.knobs.interp 没生效"
  # 旧 marker / csv 留底 (层会覆盖 marker; csv 只追加, 记起点大小)
  local PREV_SHA="" PREV_FRAMES=-1
  if asu "test -f $FILES_DIR/knobs_layer_out.json && echo y" | grep -q y; then
    asu "cp -p $FILES_DIR/knobs_layer_out.json $RUN/prev_marker_$arm.json"
    PREV_SHA=$(marker_get | shasum | cut -c1-40); PREV_FRAMES=$(marker_get | marker_frames)
  fi
  asu "wc -c < $FILES_DIR/knobs_interp_frames.csv 2>/dev/null || echo 0" > "$dir/csv_pre_size.txt"
  asu "setprop debug.vulkan.layers $LAYER_NAME"
  local T0 ACT i M SHA FR MARK_OK=0 MARK_WHY="等不到 marker"
  T0=$(date +%s)
  ACT=$(ashell "cmd package resolve-activity --brief $PKG" | tail -1)
  ashell "am start -n $ACT" >/dev/null
  for i in $(seq 1 40); do
    M=$(marker_get)
    if [ -n "$M" ]; then
      SHA=$(printf '%s' "$M" | shasum | cut -c1-40); FR=$(printf '%s' "$M" | marker_frames)
      if [ "$SHA" = "$PREV_SHA" ]; then MARK_WHY="marker 与启动前相同, 是旧的"
      elif [ "$FR" -lt 0 ]; then MARK_WHY="marker 解析不出 frames"
      elif [ "$PREV_FRAMES" -ge 0 ] && [ "$FR" -ge "$PREV_FRAMES" ] && [ "$FR" -gt 900 ]; then MARK_WHY="marker frames=$FR 没从小开始"
      else MARK_OK=1; echo "  新 marker: frames=$FR (第 $i 次查看)"; break; fi
    fi
    [ -n "$(ashell "pidof $PKG")" ] || { MARK_WHY="游戏进程没了 (崩溃或被拦)"; break; }
    sleep 2
  done
  asu "setprop debug.vulkan.layers \"$PRE_LAYERS\""
  [ "$MARK_OK" = 1 ] || { echo "  层没挂上: $MARK_WHY"; asu "logcat -d -b crash" > "$dir/logcat_crash.txt"; return 1; }
  local PID NMAP
  PID=$(ashell "pidof $PKG" | awk '{print $1}')
  NMAP=$(asu "grep -c libVkLayer_refknobs /proc/$PID/maps" | tr -d ' ')
  echo "  层已挂上 ($(( $(date +%s) - T0 )) 秒), pid=$PID, 映射段数 ${NMAP:-0}"
  [ "${NMAP:-0}" -gt 0 ] 2>/dev/null || { echo "  /proc/$PID/maps 里没有层"; return 1; }
  if [ "$arm" = B ]; then
    asu "logcat -d -b main -s refknobs:I" | grep "interp:" | tail -5 | sed 's/^/  /'
  fi
  sleep "$GATE_WAIT"
  ashell "input tap $GATE_X $GATE_Y"
  echo "  已点进门, 等 15 秒进大世界, 再采 $RUN_S 秒"
  sleep 15
  # 采样: --latency 每 2 秒一次 (每次最多 128 行, 去重后拼接), marker 每 10 秒看一眼
  local LAYERS L best="" best_n=0 n
  LAYERS=$(genshin_layers)
  echo "  原神图层候选: $(echo "$LAYERS" | tr '\n' '|')"
  : > "$dir/sf_latency_raw.txt"
  local F0 FR0 TSTART TEND
  FR0=$(marker_get | marker_frames); TSTART=$(date +%s)
  while [ $(( $(date +%s) - TSTART )) -lt "$RUN_S" ]; do
    while IFS= read -r L; do
      [ -n "$L" ] || continue
      sf_latency_sample "$L" | sed "s|^|$L\t|" >> "$dir/sf_latency_raw.txt"
    done <<< "$LAYERS"
    sleep 2
    if [ $(( ( $(date +%s) - TSTART ) % 10 )) -lt 2 ]; then
      marker_get | python3 -I -c '
import json,sys
try:
    m=json.load(sys.stdin); ip=m.get("interp"); f=m["readonly_stats"]["frames"]
    if ip: print("    frames",f,"real",ip["frames"]["real"],"synth",ip["frames"]["synth"],"real_only",ip["frames"]["real_only"],
                 "pacing",ip["pacing"],"disp_p50_ns",ip["displayed_interval_ns"].get("p50"),"fallbacks",ip["fallbacks"])
    else: print("    frames",f)
except Exception as e: print("    marker 解析失败",e)'
    fi
    [ -n "$(ashell "pidof $PKG")" ] || { echo "  游戏进程没了!"; break; }
  done
  TEND=$(date +%s); F1=$(marker_get | marker_frames)
  adb -s "$SERIAL" exec-out screencap -p > "$dir/shot_end.png" 2>/dev/null || true
  # 取证据
  marker_get > "$dir/knobs_layer_out.json"
  asu "cat $FILES_DIR/knobs_interp_frames.csv 2>/dev/null" | tail -c +$(( $(cat "$dir/csv_pre_size.txt" | tr -d ' ') + 1 )) > "$dir/knobs_interp_frames.csv"
  asu "logcat -d -b main -s refknobs:I" > "$dir/logcat_refknobs.txt"
  asu "logcat -d -b crash" > "$dir/logcat_crash.txt"
  ashell "dumpsys SurfaceFlinger --timestats -dump -maxlayers 30" > "$dir/sf_timestats.txt" 2>/dev/null
  # 来源 1: --latency (按图层分别算, 取行数最多的)
  echo "  来源 1 (--latency):"
  while IFS= read -r L; do
    [ -n "$L" ] || continue
    grep "^$L"$'\t' "$dir/sf_latency_raw.txt" | cut -f2- > "$dir/sf_latency_$(echo "$L" | tr -c 'A-Za-z0-9' '_').txt"
    n=$(grep -c . "$dir/sf_latency_$(echo "$L" | tr -c 'A-Za-z0-9' '_').txt")
    echo "    $L: $(latency_stats "$dir/sf_latency_$(echo "$L" | tr -c 'A-Za-z0-9' '_').txt")"
    [ "$n" -gt "$best_n" ] && { best_n=$n; best="$L"; }
  done <<< "$LAYERS"
  local S1="" S2="" S3=""
  [ -n "$best" ] && S1=$(latency_stats "$dir/sf_latency_$(echo "$best" | tr -c 'A-Za-z0-9' '_').txt")
  echo "  来源 2 (--timestats): $(timestats_stats "$dir/sf_timestats.txt" | tr '\n' ';')"
  S2=$(timestats_stats "$dir/sf_timestats.txt" | head -1)
  # 来源 3: 层自己
  S3=$(python3 -I - "$dir/knobs_layer_out.json" "$FR0" "$F1" "$((TEND - TSTART))" <<'PY'
import json,sys
m=json.load(open(sys.argv[1])); fr0,fr1,secs=int(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4])
ip=m.get("interp")
if ip and ip["displayed_interval_ns"].get("n",0)>=10:
    d=ip["displayed_interval_ns"]; print("layer_timing n=%d p50_ms=%.2f p95_ms=%.2f pacing=%s"%(d["n"],d["p50"]/1e6,d["p95"]/1e6,ip["pacing"]))
elif fr1>fr0 and secs>0:
    k = 2 if ip else 1   # interp 开着时每个游戏帧显示两张
    print("frames_delta n=%d mean_ms=%.2f (游戏 present %d 帧 / %d 秒 %s)"%(fr1-fr0,secs*1000.0/((fr1-fr0)*k),fr1-fr0,secs,"x2 张" if ip else ""))
else: print("无")
PY
)
  echo "  来源 3 (层): $S3"
  printf '%s\n' "latency: $S1" "timestats: $S2" "layer: $S3" > "$dir/interval_summary.txt"
  if [ "$arm" = B ]; then
    python3 -I - "$dir/knobs_layer_out.json" <<'PY'
import json,sys
ip=json.load(open(sys.argv[1])).get("interp")
if not ip: print("  B 臂 marker 里没有 interp 段!"); sys.exit(0)
f=ip["frames"]; print("  B 臂: mode=%s pacing=%s (fallback: %s / swapchain: %s)"%(ip["mode"],ip["pacing"],ip["pacing_fallback_reason"],ip["swapchain_fallback_reason"]))
print("  real=%d synth=%d real_only=%d  显示间隔 real→synth p50=%s synth→real p50=%s  late=%d early=%d  pairs=%s"%(
    f["real"],f["synth"],f["real_only"],ip["displayed_interval_real_to_synth_ns"].get("p50"),ip["displayed_interval_synth_to_real_ns"].get("p50"),
    ip["timing"]["late"],ip["timing"]["early"],ip["timing"]["pairs"]))
print("  fallbacks=%s  layer_cpu_us=%s  swapchain=%s"%(ip["fallbacks"],ip["layer_cpu_us"],ip["swapchain"]))
PY
  fi
  ashell "am force-stop $PKG"
  return 0
}

for a in $ARMS; do arm "$a" || echo "  (臂 $a 没成)"; done

# ── 判定: B 的 p50 / A 的 p50 ──
python3 -I - "$OUT" <<'PY'
import re,sys,os
out=sys.argv[1]
def p50(arm):
    p=os.path.join(out,"arm_"+arm,"interval_summary.txt")
    if not os.path.exists(p): return None,None
    for ln in open(p):
        src,_,rest=ln.partition(": ")
        m=re.search(r"p50_ms=([\d.]+)",rest) or re.search(r"mean_ms=([\d.]+)",rest)
        if m and float(m.group(1))>0: return float(m.group(1)),src+":"+rest.strip()
    return None,None
a,sa=p50("A"); b,sb=p50("B")
print("== 结论 ==")
print("A 臂显示帧间隔 p50: %s  (%s)"%(a,sa)); print("B 臂显示帧间隔 p50: %s  (%s)"%(b,sb))
if a and b:
    r=b/a; print("比值 B/A = %.3f  →  %s"%(r,"在 [0.4,0.6] 内: 显示帧间隔减半" if 0.4<=r<=0.6 else "不在 [0.4,0.6] 内: 没有减半"))
elif b:
    print("只有 B 臂: p50=%.2f ms  →  %s"%(b,"接近 16.7 ms (60 Hz 显示)" if 14<=b<=19.5 else "不是 60 Hz 节奏"))
else: print("没有可用数据 (三条来源都空), 看 arm_*/logcat_*.txt")
PY
echo "证据在 $OUT"
# EXIT trap 做还原
