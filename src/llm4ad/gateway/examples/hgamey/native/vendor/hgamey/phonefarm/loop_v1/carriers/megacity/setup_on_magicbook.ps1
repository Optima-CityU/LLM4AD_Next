# setup_on_magicbook.ps1
#
# 在构建机 (magicbook) 上准备 megacity 载体的工程: 克隆上游 → 注入胶水 → 自检。
# 只做准备, 不出包; 出包走 build_megacity.ps1。
#
# 前置: 用户已在 Unity Hub 登录 Unity ID 并激活 Personal 许可证, 且已装
#       Unity 6000.1.0f1 + Android Build Support (含 OpenJDK / SDK / NDK)。
#       这一步是人工的, 脚本不替代也不检测许可证状态。
#
# 用法:
#   pwsh -File setup_on_magicbook.ps1 -ThirdParty C:\thirdparty -Glue <本目录路径>
#
# !! 状态: 未执行验证 !! 写的时候 magicbook 上还没装 Unity, 本脚本一次都没跑过。

[CmdletBinding()]
param(
    [string]$ThirdParty = "C:\thirdparty",
    [string]$Glue       = $PSScriptRoot,
    [string]$Ref        = "master"
)

$ErrorActionPreference = "Stop"
$ExpectedUnity = "6000.1.0f1"
$Repo    = "https://github.com/Unity-Technologies/megacity-metro.git"
$ProjDir = Join-Path $ThirdParty "megacity-metro"

function Step($m) { Write-Host "[setup] $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "[setup] 停: $m" -ForegroundColor Red; exit 1 }

# 0) git-lfs 必须在 clone 之前就位, 否则拉下来的是一堆 pointer 文件
Step "检查 git-lfs"
try { git lfs version | Out-Null } catch { Die "git-lfs 不可用。上游用 LFS 存美术资源, 没有它克隆出来的工程打不开。" }

# 1) 克隆 (幂等: 已存在就只 fetch, 不动工作区)
if (Test-Path $ProjDir) {
    Step "工程已存在, 跳过克隆: $ProjDir"
} else {
    Step "克隆上游到 $ProjDir (含 LFS, 约 0.6 GB)"
    New-Item -ItemType Directory -Force -Path $ThirdParty | Out-Null
    git clone --depth 1 --branch $Ref $Repo $ProjDir
    if ($LASTEXITCODE -ne 0) { Die "克隆失败" }
}

# 2) 版本核对 —— 上游换 Unity 版本时要人看见, 不要默默用别的编辑器打开
$pv = Join-Path $ProjDir "ProjectSettings\ProjectVersion.txt"
if (-not (Test-Path $pv)) { Die "找不到 ProjectVersion.txt, 克隆不完整" }
$ver = (Select-String -Path $pv -Pattern 'm_EditorVersion:\s*(\S+)').Matches[0].Groups[1].Value
Step "上游要求 Unity $ver"
if ($ver -ne $ExpectedUnity) {
    Die "上游 Unity 版本变成了 $ver, 本套胶水是按 $ExpectedUnity 写的。先人工核对再改脚本, 不要用别的版本硬开。"
}

# 3) 注入胶水。只新增文件, 不改上游任何既有文件 —— 上游更新时这套东西不会冲突。
$scriptsDir = Join-Path $ProjDir "Assets\Scripts\Refbench"
$editorDir  = Join-Path $ProjDir "Assets\Editor"
New-Item -ItemType Directory -Force -Path $scriptsDir, $editorDir | Out-Null

Step "注入 harness 与构建脚本"
Copy-Item (Join-Path $Glue "unity\MegacityRefbenchHarness.cs") $scriptsDir -Force
Copy-Item (Join-Path $Glue "unity\UpstreamCommit.cs")          $scriptsDir -Force
Copy-Item (Join-Path $Glue "unity\MegacityRefbenchBuild.cs")   $editorDir  -Force

# 4) 路线随包兜底一份进 StreamingAssets (设备上 adb push 的那份优先)
$routeSrc = Join-Path $Glue "routes\route_a.json"
if (Test-Path $routeSrc) {
    $sa = Join-Path $ProjDir "Assets\StreamingAssets\refbench_routes"
    New-Item -ItemType Directory -Force -Path $sa | Out-Null
    Copy-Item $routeSrc $sa -Force
    Step "路线已随包: route_a.json"
} else {
    Write-Host "[setup] 注意: 还没有 routes\route_a.json (只有 .example)。" -ForegroundColor Yellow
    Write-Host "        包照样能出, 但跑的时候 harness 会因为找不到路线而 clean_exit=false。" -ForegroundColor Yellow
    Write-Host "        标定步骤见 README 的「路线标定」。" -ForegroundColor Yellow
}

# 5) 记录上游 commit, 出包时钉进 APK
Push-Location $ProjDir
$commit = (git rev-parse HEAD).Trim()
Pop-Location
Set-Content -Path (Join-Path $Glue "upstream_commit.txt") -Value $commit
Step "上游 commit = $commit"

Step "准备完成。出包: build_megacity.ps1 -ProjDir $ProjDir"
