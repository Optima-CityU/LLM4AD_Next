param(
    [ValidateSet("infra", "full", "stop", "remove", "logs", "ps")]
    [string]$Command = "infra",

    [switch]$DryRun,

    [switch]$Help,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Services = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Usage {
    @"
Usage: .\dev.ps1 [infra|full|stop|remove|logs|ps] [-DryRun] [service...]

Local Docker helper for contributors.

Commands:
  infra    Start local infrastructure only for host-run backend/frontend.
  full     Build and start the full stack from local source with debug ports.
  stop     Stop containers created by either local mode.
  remove   Remove containers created by either local mode; keeps bind-mounted data.
  logs     Follow compose logs. Optional service names are accepted.
  ps       Show compose service status.

Options:
  -DryRun  Print commands without running them.
  -Help    Show this help.

Before first use:
  Copy-Item .env.develop.local.example .env
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Require-EnvFile {
    if ($DryRun) {
        return
    }

    if (-not (Test-Path ".env" -PathType Leaf)) {
        Write-Error @"
Missing docker/.env. Create it first:

  cd docker
  Copy-Item .env.develop.local.example .env

Then edit required secrets and local paths before starting Docker services.
"@
    }
}

function Format-Arg {
    param([string]$Arg)

    if ($Arg -match '[\s"`$]') {
        return '"' + ($Arg -replace '"', '\"') + '"'
    }

    return $Arg
}

function Invoke-Native {
    param(
        [string]$Executable,
        [string[]]$Arguments
    )

    if ($DryRun) {
        $Printable = @($Executable) + $Arguments | ForEach-Object { Format-Arg $_ }
        Write-Host ("+ " + ($Printable -join " "))
        return
    }

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-Compose {
    param([string[]]$Arguments)
    Invoke-Native "docker" (@("compose") + $Arguments)
}

$LocalCompose = @("-f", "compose.yml", "-f", "compose.override.yml", "--profile", "debug")
$FullCompose = @("-f", "compose.yml", "-f", "compose.mindmemos.yml", "-f", "compose.mindmemos.debug.yml", "-f", "compose.deploy.debug.yml", "--profile", "debug")
$InfraServices = @("db", "redis", "rustfs", "adminer", "mailcatcher", "code_server_proxy", "task-runner")

switch ($Command) {
    "infra" {
        Require-EnvFile
        Invoke-Compose ($LocalCompose + @("up", "-d", "--build") + $InfraServices)
    }
    "full" {
        Require-EnvFile
        Invoke-Compose ($FullCompose + @("up", "-d", "--build"))
    }
    "stop" {
        Require-EnvFile
        Invoke-Compose ($FullCompose + @("stop"))
        Invoke-Compose ($LocalCompose + @("stop"))
    }
    "remove" {
        Require-EnvFile
        Invoke-Compose ($FullCompose + @("down", "--remove-orphans"))
        Invoke-Compose ($LocalCompose + @("down", "--remove-orphans"))
    }
    "logs" {
        Require-EnvFile
        Invoke-Compose ($FullCompose + @("logs", "-f") + $Services)
    }
    "ps" {
        Require-EnvFile
        Invoke-Compose ($FullCompose + @("ps"))
    }
}
