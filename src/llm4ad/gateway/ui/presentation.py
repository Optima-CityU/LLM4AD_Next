"""Local styling and bilingual copy for a compact research console."""

CSS = """
:root { --lab-bg:#f4f5f2; --lab-panel:#fff; --lab-ink:#23312e; --lab-muted:#63716b; --lab-line:#dde2dc; }
body.body--dark { --lab-bg:#141c1b; --lab-panel:#1d2725; --lab-ink:#e4ebe5; --lab-muted:#a2b1a7; --lab-line:#35423d; }
body { background:var(--lab-bg); color:var(--lab-ink); font-family:'Aptos','Segoe UI','Noto Sans CJK SC',sans-serif; }
.nicegui-content { padding:0; gap:0; }
.lab-shell { width:min(1320px,100%); margin:auto; padding:30px 36px 48px; gap:22px; }
.lab-header { background:var(--lab-panel); color:var(--lab-ink); border-bottom:1px solid var(--lab-line); }
.lab-header-inner { width:min(1320px,100%); margin:auto; padding:12px 36px; align-items:center; gap:24px; flex-wrap:wrap; }
.lab-brand { font-family:Georgia,'Noto Serif CJK SC',serif; font-size:22px; font-weight:600; letter-spacing:-.6px; }
.lab-eyebrow { color:var(--lab-muted); font-size:11px; letter-spacing:1.7px; text-transform:uppercase; }
.lab-nav { gap:6px; flex:1; }
.lab-title { font-family:Georgia,'Noto Serif CJK SC',serif; font-size:34px; line-height:1.2; letter-spacing:-.6px; }
.lab-muted { color:var(--lab-muted); font-size:13px; }
.lab-panel { width:100%; background:var(--lab-panel); border:1px solid var(--lab-line);
border-radius:8px; padding:22px; box-shadow:none; gap:14px; }
.lab-grid { display:grid; width:100%; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }
.lab-stat { font-size:30px; line-height:1.2; font-variant-numeric:tabular-nums; }
.lab-row { width:100%; align-items:center; gap:12px; }
.lab-between { justify-content:space-between; }
.lab-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); width:100%; gap:18px; }
.lab-form .q-field { min-width:0; }
.lab-table { width:100%; background:var(--lab-panel); box-shadow:none; border:1px solid var(--lab-line); border-radius:8px; }
.lab-table th { color:var(--lab-muted); font-size:11px; letter-spacing:.5px; }
.lab-table td { font-size:13px; }
.lab-code { width:100%; white-space:pre-wrap; overflow-wrap:anywhere;
font-family:'Cascadia Code','SFMono-Regular',Consolas,monospace; font-size:12px; line-height:1.65; }
.lab-log { max-height:500px; overflow:auto; width:100%; gap:10px; }
.lab-log-entry { width:100%; padding:10px 12px; border-left:2px solid var(--lab-line); }
.lab-artifact { width:100%; padding:12px 0; border-bottom:1px solid var(--lab-line); gap:8px; }
.lab-pill { font-size:11px; letter-spacing:.3px; }
.lab-warning { border-left:3px solid #bf8b32; }
.lab-error { border-left:3px solid #bf4d40; }
.lab-grow { flex:1; min-width:0; }
.lab-link { color:#277a6c; }
.lab-divider { border-top:1px solid var(--lab-line); width:100%; }
.lab-footer { padding-top:10px; border-top:1px solid var(--lab-line); }
.lab-secondary-stats { gap:12px 26px; padding:0 3px; }
.lab-transport { width:100%; padding:8px 0 16px; gap:7px; border-bottom:1px solid var(--lab-line); }
.lab-transport-label { width:108px; font-size:12px; }
.lab-confirm { width:560px; max-width:94vw; padding:26px; gap:16px; }
@media(max-width:850px) { .lab-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.lab-shell{padding:22px 18px} .lab-header-inner{padding:12px 18px;gap:12px} }
@media(max-width:540px) { .lab-form{grid-template-columns:1fr} .lab-title{font-size:28px} .lab-nav{order:3;flex-basis:100%} }
@media(prefers-reduced-motion:reduce) { *,*::before,*::after{animation:none!important;transition:none!important} }
:root { --lab-link:#246f61; }
body.body--dark { --lab-link:#80dbc4; }
body { font-size:14px; }
a,.lab-link { color:var(--lab-link); }
body.body--dark .q-btn--flat.text-primary { color:var(--lab-link)!important; }
.lab-title { margin:0; }
.lab-section-title { font-size:16px; font-weight:600; margin:0; }
.lab-table td { font-size:14px; overflow-wrap:anywhere; }
.lab-table th { font-size:12px; }
.lab-table { min-width:0; max-width:100%; overflow:hidden; }
.lab-table .q-table__middle { max-width:100%; overflow-x:auto; overscroll-behavior-x:contain; }
.lab-task-table table { min-width:900px; }
.lab-task-table td.lab-started-cell,.lab-task-table th.lab-started-cell {
min-width:184px; width:184px; white-space:nowrap!important; overflow-wrap:normal; }
.lab-task-table td.lab-duration-cell,.lab-task-table th.lab-duration-cell {
min-width:112px; width:112px; white-space:nowrap!important; overflow-wrap:normal; }
.lab-task-table td.lab-state-cell,.lab-task-table th.lab-state-cell {
min-width:180px; width:180px; white-space:nowrap!important; overflow-wrap:normal; }
.lab-task-table .lab-phase { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.lab-table .lab-actions-cell { position:sticky; right:0; z-index:2; background:var(--lab-panel);
box-shadow:-3px 0 5px #0000000a; white-space:nowrap; width:192px; min-width:192px; }
.lab-table thead .lab-actions-cell { z-index:3; }
.lab-table .lab-algorithm { min-width:210px; max-width:330px; }
.lab-phase { display:block; max-width:230px; margin-top:5px; color:var(--lab-muted); font-size:12px; }
.lab-dialog { width:min(1100px,96vw); max-width:96vw!important; max-height:92dvh;
background:var(--lab-panel); color:var(--lab-ink); padding:0; gap:0; }
.lab-dialog-header { width:100%; padding:16px 22px; border-bottom:1px solid var(--lab-line); flex-shrink:0; }
.lab-dialog-body { width:100%; min-height:0; padding:20px 22px; gap:14px; overflow:auto; }
.lab-scroll { width:100%; height:52dvh; min-height:180px; border:1px solid var(--lab-line); border-radius:5px; }
.lab-records { width:100%; padding:10px 14px; gap:0; }
.lab-record { width:100%; padding:9px 0; border-bottom:1px solid var(--lab-line); gap:3px; }
.lab-code { min-width:0; max-width:100%; tab-size:4; }
.lab-code { font-size:14px; line-height:1.6; }
.lab-file-preview { width:100%; border:1px solid var(--lab-line); padding:14px; border-radius:5px; }
.lab-kv { width:100%; display:grid; grid-template-columns:minmax(130px,210px) minmax(0,1fr); gap:10px 18px; }
.lab-kv > * { min-width:0; overflow-wrap:anywhere; }
.lab-kv-key { color:var(--lab-muted); }
.lab-inline-error { color:#b64e42; white-space:pre-wrap; overflow-wrap:anywhere; }
body.body--dark .lab-inline-error { color:#ffa99e; }
.lab-diff { width:100%; border-collapse:collapse; }
.lab-diff td,.lab-diff th { padding:9px 12px; border-bottom:1px solid var(--lab-line); text-align:left; overflow-wrap:anywhere; }
.lab-mobile-action { display:none; }
.lab-dialog .q-table__container { max-width:100%; }
@media(max-width:650px) { .lab-desktop-action{display:none!important} .lab-mobile-action{display:block}
.lab-table .lab-actions-cell{width:116px;min-width:116px} .lab-resource-cell{display:none}
.lab-dialog{max-height:96dvh} .lab-dialog-body{padding:14px} .lab-kv{grid-template-columns:1fr;gap:3px}
.lab-kv-key:not(:first-child){margin-top:10px} }
"""

CANCELLATION_WARNING = (
    "This requests cancellation of this evaluation attempt only. Existing cancellation behavior propagates "
    "to the parent evolution and may stop it. This is not a skip action."
)
SETTINGS_NOTICE = "Changes may wait for active evaluations to finish. Listener changes require a dashboard restart."

STATE_LABELS = {
    "preparing": "Preparing",
    "evaluating": "Evaluating",
    "finalizing": "Finalizing",
    "configuration_required": "Setup required",
    "draining_for_settings": "Waiting to apply settings",
    "ready": "Ready",
    "running": "Running",
    "connected": "Connected",
    "completed": "Completed",
    "pending": "Local queued",
    "queued": "Local queued",
    "success": "Success",
    "recovery_required": "Recovery required",
    "pending_upload": "Pending upload",
    "accepted": "Accepted",
    "starting": "Starting",
    "checking": "Checking",
    "paused": "Paused",
    "cancel_requested": "Cancellation requested",
    "cancelling": "Cancelling",
    "unready": "Not ready",
    "failed": "Failed",
    "error": "Error",
    "cancelled": "Cancelled",
    "stopped": "Stopped",
    "disconnected": "Disconnected",
    "reconnecting": "Reconnecting",
}

ZH = {
    "Status": "状态",
    "Ready": "就绪",
    "Backend": "执行后端",
    "Evaluator": "评估器",
    "Platform": "平台",
    "Startup checks are local records; they are not platform task reports.": "启动检查记录保存在本地，不属于平台任务上报。",
    "compile": "编译",
    "check": "检查",
    "startup_check": "启动检查",
    "Log expired": "日志已过期",
    "Failed during": "失败阶段：",
    "preparing": "准备",
    "evaluating": "评估",
    "finalizing": "收尾",
    "Platform reporting records": "平台上报记录",
    "Local runtime logs": "本地运行日志",
    "These records describe reports and events sent to the platform.": "此处展示平台上报记录与事件。",
    "Local stdout and stderr are stored separately from platform reporting records.": (
        "本地标准输出、标准错误与平台上报记录分别保存。"
    ),
    "Files": "文件",
    "Execution information": "执行信息",
    "Diagnostics": "诊断信息",
    "Copy IDs": "复制标识符",
    "IDs copied": "已复制标识符",
    "Copy exact value": "复制原始精度值",
    "Value copied": "值已复制",
    "Close": "关闭",
    "Actions": "操作",
    "More actions": "更多操作",
    "Resource": "资源",
    "All resources": "所有资源",
    "Started": "开始时间",
    "Finished": "结束时间",
    "Duration": "耗时",
    "Lifecycle phase": "生命周期阶段",
    "Not recorded": "未记录",
    "Evaluation": "评估",
    "Load older records": "加载更早记录",
    "Load earlier output": "加载更早输出",
    "Follow new records": "跟随新记录",
    "Follow new output": "跟随新输出",
    "Level": "级别",
    "All levels": "所有级别",
    "Search records": "搜索记录",
    "Source": "日志来源",
    "Log no longer available": "日志已不可用",
    "No local logs recorded": "暂无本地日志",
    "Log was rotated or truncated; showing the current file.": "日志已轮转或截断，现展示当前文件。",
    "Download log snapshot": "下载日志快照",
    "Available": "可用",
    "Unavailable": "不可用",
    "Uploaded": "已上传",
    "Not uploaded": "未上传",
    "Size": "大小",
    "Name": "名称",
    "Metric": "指标名称",
    "Value": "值",
    "No metrics recorded": "暂无指标记录",
    "Select a file to preview.": "选择一个文件进行预览。",
    "Clear preview": "关闭预览",
    "Startup check": "启动检查",
    "Startup check records": "启动检查记录",
    "Startup check is not recorded locally.": "本地尚未记录启动检查。",
    "Evaluator source": "评估器来源",
    "Custom evaluator": "自定义评估器",
    "Built-in example": "内置示例评估器",
    "HGamey shader evaluator": "HGamey 着色器评估器",
    "Local log retention (seconds)": "本地日志保留时长（秒）",
    "Local log retention": "本地日志保留",
    "Applied settings": "当前生效设置",
    "Setting": "设置项",
    "Applied": "已生效",
    "Pending": "待生效",
    "Settings applied": "设置已生效",
    "Settings will apply after active evaluations finish.": "正在执行的评估结束后将应用设置。",
    "Saved listener settings require a restart.": "已保存的监听设置需要重启后生效。",
    "No pending changes": "没有待生效更改",
    "Please correct the highlighted fields.": "请修正标记的字段。",
    "Required": "必填",
    "A custom evaluator import is required.": "请填写自定义评估器导入路径。",
    "At least one allowed user is required.": "至少需要一个允许的用户。",
    "Current evaluator import": "当前评估器导入路径",
    "Read-only built-in evaluator import.": "内置评估器导入路径只读。",
    "Admission": "任务接收",
    "Capacity": "并发容量",
    "Evidence cleanup": "证据清理",
    "Enabled": "已启用",
    "Disabled": "已关闭",
    "Current attempt unavailable": "本次执行尝试已不可用",
    "Task": "任务",
    "Timeout": "超时时间",
    "Purpose": "用途",
    "task": "评估任务",
    "startup": "启动检查",
    "Progress": "进度",
    "Overview": "概览",
    "Tasks": "任务",
    "Settings": "设置",
    "Gateway console": "网关控制台",
    "Evaluation gateway": "评估网关",
    "Live service state and evaluation activity.": "服务实时状态与评估活动。",
    "Pause intake": "暂停接收",
    "Resume intake": "恢复接收",
    "Paused": "已暂停",
    "Active": "接收中",
    "Service": "服务",
    "Bindings": "评估器绑定",
    "No bindings yet": "暂无评估器绑定",
    "Queued": "排队中",
    "Local queued": "本地排队",
    "Success": "成功",
    "Algorithm": "算法",
    "Unnamed algorithm": "未命名算法",
    "Times follow your browser's time zone.": "时间使用浏览器所在时区。",
    "Registration": "注册状态",
    "Heartbeat": "心跳",
    "Last success": "最近成功",
    "Last contact": "最近联系",
    "Notifications": "通知连接",
    "reconnecting": "重新连接中",
    "Preview": "预览",
    "Load preview": "加载预览",
    "Plain-text preview. Download the original for the complete file.": "纯文本预览；请下载原始文件以查看完整内容。",
    "Preview limited to the first 2 MiB. Download the original for the complete file.": (
        "仅预览前 2 MiB；请下载原始文件以查看完整内容。"
    ),
    "Preview unavailable": "预览不可用",
    "Running": "运行中",
    "Completed": "已完成",
    "Failed": "失败",
    "Gateway status": "网关状态",
    "Connection": "连接状态",
    "Summary": "汇总",
    "No data yet": "暂无数据",
    "No evaluations match these filters.": "没有符合筛选条件的评估。",
    "Search jobs": "搜索任务",
    "All states": "所有状态",
    "All bindings": "所有绑定",
    "Binding": "绑定",
    "Job": "任务",
    "Attempt": "尝试",
    "State": "状态",
    "Updated": "更新时间",
    "Details": "详情",
    "Previous": "上一页",
    "Next": "下一页",
    "Page": "页",
    "Total": "总计",
    "Recorded evaluations, scoped to each execution attempt.": "按每次执行尝试查看评估记录。",
    "Task details": "任务详情",
    "Back to tasks": "返回任务列表",
    "Cancel evaluation": "取消评估",
    "Cancel this evaluation?": "取消本次评估？",
    "Keep running": "继续运行",
    "Confirm cancellation": "确认取消",
    CANCELLATION_WARNING: ("仅请求取消本次评估尝试。现有取消机制会传播到父级演化任务，并可能使其停止。这不是跳过操作。"),
    "Cancellation requested": "已请求取消",
    "Metrics": "指标",
    "Metadata": "元数据",
    "Logs": "日志",
    "Artifacts": "产物",
    "Download": "下载",
    "No artifacts recorded": "暂无产物记录",
    "No messages recorded": "暂无日志记录",
    "No description provided": "未提供说明",
    "Report": "评估报告",
    "Universal gateway settings.": "通用网关设置。",
    "Connection and evaluator": "连接与评估器",
    "Server URL": "服务端地址",
    "Gateway name": "网关名称",
    "Allowed users": "允许的用户",
    "One user per line, or comma-separated.": "每行一个用户，也可使用逗号分隔。",
    "Adapter": "评估器来源",
    "Evaluator import": "评估器导入路径",
    "State directory": "状态目录",
    "Concurrency": "并发数",
    "HGamey runs one evaluation at a time.": "HGamey 固定串行执行评估。",
    "Evidence retention": "证据保留",
    "Enable cleanup": "启用清理",
    "Cleanup interval (seconds)": "清理间隔（秒）",
    "Keep evidence (seconds)": "证据保留时长（秒）",
    "Dashboard preferences": "控制台偏好",
    "Listen host": "监听地址",
    "Listen port": "监听端口",
    "Open browser": "自动打开浏览器",
    "Language": "语言",
    "Theme": "主题",
    "Auto": "自动",
    "Yes": "是",
    "No": "否",
    "Light": "浅色",
    "Dark": "深色",
    "Apply settings": "应用设置",
    "Discard pending": "放弃待生效设置",
    "Pending settings": "待生效设置",
    "Settings saved": "设置已保存",
    "Pending settings discarded": "待生效设置已放弃",
    SETTINGS_NOTICE: "部分更改需等待正在执行的评估结束；监听地址和端口更改需重启控制台。",
    "Browser tabs do not control the gateway lifetime.": "关闭浏览器标签页不会停止网关。",
    "Unable to refresh. Retrying automatically.": "刷新失败，将自动重试。",
    "Operation failed": "操作失败",
    "Sign out": "退出登录",
    "Dashboard password": "控制台密码",
    "Sign in": "登录",
    "Independent dashboard access": "独立的控制台访问验证",
    "Incorrect password": "密码错误",
    "Try again shortly": "请稍后重试",
    "Server is starting": "服务正在启动",
    "ready": "就绪",
    "running": "运行中",
    "connected": "已连接",
    "completed": "已完成",
    "pending": "排队中",
    "success": "成功",
    "recovery_required": "需要恢复",
    "pending_upload": "等待上传",
    "configuration_required": "需要配置",
    "draining_for_settings": "等待任务结束以应用设置",
    "Configure allowed users and an evaluator to start the gateway.": "请配置允许的用户和评估器以启动网关。",
    "Restart required to apply the listener settings.": "需重启控制台以应用监听设置。",
    "Artifact no longer available locally": "本地产物已不可用",
    "queued": "排队中",
    "accepted": "已接收",
    "starting": "启动中",
    "checking": "检查中",
    "paused": "已暂停",
    "cancel_requested": "已请求取消",
    "cancelling": "取消中",
    "unready": "未就绪",
    "failed": "失败",
    "error": "错误",
    "cancelled": "已取消",
    "stopped": "已停止",
    "disconnected": "已断开",
}


def translate(language: str, text: str) -> str:
    """Translate known dashboard copy while preserving unknown service states."""
    return ZH.get(text, STATE_LABELS.get(text, text)) if language == "zh" else STATE_LABELS.get(text, text)
