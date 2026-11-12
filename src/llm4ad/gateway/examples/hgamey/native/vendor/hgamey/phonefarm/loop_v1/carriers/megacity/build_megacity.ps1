# build_megacity.ps1
#
# 在构建机 (magicbook) 上零交互出 arm64 / Vulkan / IL2CPP 的 APK。
#
# 用法:
#   pwsh -File build_megacity.ps1 -ProjDir C:\thirdparty\megacity-metro
#
# 产物: <Glue>\out\megacity.apk   (out\ 已在 .gitignore 里, 不进仓库)
#
# !! 状态: 未执行验证 !! magicbook 上还没装 Unity, 本脚本一次都没跑过。

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProjDir,
    [string]$Glue      = $PSScriptRoot,
    [string]$UnityExe  = "",
    [string]$OutApk    = ""
)

$ErrorActionPreference = "Stop"
$ExpectedUnity = "6000.1.0f1"

function Step($m) { Write-Host "[build] $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "[build] 停: $m" -ForegroundColor Red; exit 1 }

if (-not $OutApk) { $OutApk = Join-Path $Glue "out\megacity.apk" }
New-Item -ItemType Directory -Force -Path (Split-Path $OutApk) | Out-Null

# 1) 定位编辑器。不猜版本 —— 版本不对就停。
if (-not $UnityExe) {
    $UnityExe = "C:\Program Files\Unity\Hub\Editor\$ExpectedUnity\Editor\Unity.exe"
}
if (-not (Test-Path $UnityExe)) {
    Die "找不到 Unity $ExpectedUnity : $UnityExe`n    (Hub 里装好 6000.1.0f1 + Android Build Support 后再跑, 或用 -UnityExe 显式指定)"
}
Step "编辑器: $UnityExe"

# 2) 上游 commit, 钉进包里
Push-Location $ProjDir
$commit = (git rev-parse HEAD).Trim()
Pop-Location
Step "上游 commit: $commit"

# 3) 批处理构建
$log = Join-Path $Glue "out\build.log"
if (Test-Path $log) { Remove-Item $log -Force }
Step "开始构建 (日志: $log)"

$args = @(
    "-quit", "-batchmode", "-nographics",
    "-projectPath", $ProjDir,
    "-executeMethod", "Megacity.Refbench.MegacityRefbenchBuild.BuildAndroid",
    "-buildOut", $OutApk,
    "-upstreamCommit", $commit,
    "-logFile", $log
)
$p = Start-Process -FilePath $UnityExe -ArgumentList $args -Wait -PassThru -NoNewWindow
$code = $p.ExitCode

# 4) 退出码语义与 MegacityRefbenchBuild 对齐: 0 成功 / 1 构建失败 / 3 构建后自检不通过
switch ($code) {
    0 { }
    1 { Die "构建失败, 看日志尾部:`n" + (Get-Content $log -Tail 40 | Out-String) }
    3 { Die "构建后自检不通过 (图形 API / 架构 / 后端 有一项不对), 看日志:`n" + (Get-Content $log -Tail 40 | Out-String) }
    default { Die "Unity 退出码 $code, 看日志: $log" }
}

if (-not (Test-Path $OutApk)) { Die "退出码是 0 但产物不存在: $OutApk" }

$size = [math]::Round((Get-Item $OutApk).Length / 1MB, 1)
Step "出包成功: $OutApk ($size MB)"
Step "接下来把 APK 传回采集机 (手机只连那台), 再 adb install -r"
