#!/bin/bash
# round.sh <轮次> — 单轮端到端: 设备体检→清应用→起头条→跑一局→汇总一行进 campaign10.tsv
# 由外层逐轮调度;caffeinate 包在本脚本外层防休眠。
N=$1
ROOT="$(cd "$(dirname "$0")" && pwd)"  # 自动定位仓库根目录,搬家/克隆后无需改
export PATH="/Users/mac/Library/Application Support/CindyGlobal/android-platform-tools/darwin-arm64/platform-tools:$PATH"
source "$ROOT/secrets.env"  # GLM_KEY / EMPERO_KEY 从本地密钥文件读取,不进 git
TASK="今日头条遍历"
SER=emulator-5554
PKG=com.ss.android.article.news
cd "$ROOT" || exit 9

GOAL=$(cat <<'GEOF'
这是真机原生App今日头条(新闻资讯类,流氓软件样本)。目标:深度遍历它的全部主要功能页面,产出完整页面清单。规则:①各类弹窗(协议/权限/加桌面组件/广告/升级)按'同意进入、拒绝授权、关闭×'处理;全程不登录账号——凡进入登录页只记'需登录'后立即用关闭或返回退出,绝不反复重试同一入口;②底部五标签(首页/视频/赚钱/商城/我的)逐一进入并探索子功能;③首页顶部频道标签(推荐/热榜/小说/发现/视频等)逐个切换,每个到达一次即可,不深刷信息流;④'视频'页看顶部子标签;'赚钱'页看主要入口(不点提现/领取);'商城'页看主要分区;⑤'我的'页:进设置,逐项查看设置子页(如隐私中心/个人信息收集/通知设置/清除缓存),以及其他无需登录可见的子页;⑥从首页搜索框进搜索页看一眼即可返回(不输入);⑦feed里视频自动播放导致画面常动是正常现象,系统已按扣除背景判断动作效果,继续遍历即可,确认是视频页可在note记下;⑧每到新页面在note更新'已访问清单',到不了的记'跳过:原因';⑨三类区域都覆盖后,先回到'我的'或设置页再done,让最终画面与覆盖清单对应。
GEOF
)

SUM="$ROOT/tasks/$TASK/campaign10.tsv"
OUT="$ROOT/tasks/$TASK/campaign10.out"
touch "$OUT"
[ -f "$SUM" ] || printf "round\trun\texit\tstop\tachieved\tsteps\tcalls\twall\n" > "$SUM"

health_ok() {
  local r
  r=$(perl -e 'alarm 15; exec @ARGV' adb -s $SER shell echo ok 2>/dev/null | tr -d '\r')
  [ "$r" = "ok" ]
}

restart_emulator() {
  echo "[round$N] $(date +%H:%M:%S) 设备无响应,重启模拟器" >> "$OUT"
  pkill -f "avd agentphone"
  sleep 3
  adb kill-server >/dev/null 2>&1
  adb start-server >/dev/null 2>&1
  nohup /opt/homebrew/share/android-commandlinetools/emulator/emulator -avd agentphone \
    >/tmp/phonefarm-emu.log 2>&1 &
  disown
  adb wait-for-device 2>/dev/null
  local i=0
  while [ "$(adb -s $SER shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ] && [ $i -lt 90 ]; do
    sleep 2; i=$((i + 1))
  done
  sleep 5
}

# ── 体检,不行就救 ──
health_ok || restart_emulator
if ! health_ok; then
  printf "%s\t-\t9\tEMULATOR_DEAD\t-\t0\t0\t0s\n" "$N" >> "$SUM"
  exit 9
fi

# ── OCR文字备胎(UI树为空时用): 没编译过就编一次 ──
[ -x "$ROOT/ocr" ] || swiftc -O "$ROOT/ocr.swift" -o "$ROOT/ocr"

# ── 轮间清理: 强停应用回桌面,统一从 MainActivity 起 ──
adb -s $SER shell am force-stop $PKG
adb -s $SER shell input keyevent 3
sleep 3
adb -s $SER shell am start -n $PKG/$PKG.activity.MainActivity >/dev/null 2>&1
sleep 6

# ── 跑一局 ──
t0=$(date +%s)
echo "[round$N] $(date +%H:%M:%S) 开跑" >> "$OUT"
./phonefarm run --task "$TASK" --endless --budget-calls 90 --app "$PKG" "$GOAL" >> "$OUT" 2>&1
rc=$?
wall=$(( $(date +%s) - t0 ))

# ── 汇总 ──
RUN=$(ls -1 "$ROOT/tasks/$TASK/runs" | sort | tail -1)
S=$(python3 "$ROOT/summarize_run.py" "$ROOT/tasks/$TASK/runs/$RUN/log.jsonl" 2>/dev/null || printf "?\t?\t?\t?")
printf "%s\t%s\t%s\t%s\t%ss\n" "$N" "$RUN" "$rc" "$S" "$wall" >> "$SUM"
echo "[round$N] $(date +%H:%M:%S) 结束 rc=$rc wall=${wall}s" >> "$OUT"
exit $rc
