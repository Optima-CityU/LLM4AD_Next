[CmdletBinding()]
param(
    [string]$RunRoot = "",
    [ValidateRange(1, 1000)]
    [int]$PopulationSize = 2,
    [ValidateRange(1, 1000)]
    [int]$MaxSamples = 3,
    [ValidateRange(1, 100)]
    [int]$TopK = 2,
    [string]$CandidatePython = "",
    [string]$ConfirmBy = "LLM4AD CSPaper ALNS example",
    [switch]$RegenerateData,
    [switch]$Evolve
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-NativeSuccess {
    param([Parameter(Mandatory = $true)][string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

$ExampleRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ExampleRoot "..\..\..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Llm4ad = Join-Path $RepoRoot ".venv\Scripts\llm4ad.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Repository Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $Llm4ad -PathType Leaf)) {
    throw "LLM4AD CLI was not found: $Llm4ad"
}

if ([string]::IsNullOrWhiteSpace($RunRoot)) {
    $RunsParent = Join-Path (Split-Path $RepoRoot -Parent) "LLM4AD_Next-demo-runs"
    $RunRoot = Join-Path $RunsParent ("alns-paper-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$RunRoot = [IO.Path]::GetFullPath($RunRoot)
New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null

$TranscriptPath = Join-Path $RunRoot "run-transcript.log"
$TranscriptStarted = $false
$Succeeded = $false

try {
    Start-Transcript -Path $TranscriptPath -Force | Out-Null
    $TranscriptStarted = $true

    Write-Step "1/8 Copy original paper, review, baseline source, and datasets"
    foreach ($FileName in @(
        "config.yaml",
        "dataset-manifest.json",
        "generate_tsp_datasets.py",
        "paper.pdf",
        "review.md",
        "task_evaluator.py"
    )) {
        Copy-Item -LiteralPath (Join-Path $ExampleRoot $FileName) -Destination $RunRoot -Force
    }
    foreach ($DirectoryName in @("algorithm", "data", "private-test")) {
        Copy-Item -LiteralPath (Join-Path $ExampleRoot $DirectoryName) `
            -Destination $RunRoot -Recurse -Force
    }

    if ([string]::IsNullOrWhiteSpace($CandidatePython)) {
        if (-not [string]::IsNullOrWhiteSpace($env:TSP_EVALUATOR_PYTHON)) {
            $CandidatePython = $env:TSP_EVALUATOR_PYTHON
        }
        else {
            $DatawhalePython = "C:\Users\lenovo\miniconda3\envs\Datawhale\python.exe"
            $CandidatePython = if (Test-Path -LiteralPath $DatawhalePython) {
                $DatawhalePython
            }
            else {
                $Python
            }
        }
    }
    $CandidatePython = [IO.Path]::GetFullPath($CandidatePython)
    if (-not (Test-Path -LiteralPath $CandidatePython -PathType Leaf)) {
        throw "Candidate Python was not found: $CandidatePython"
    }
    & $CandidatePython -c "import alns, numpy, tsplib95"
    if ($LASTEXITCODE -ne 0) {
        throw "Candidate dependencies are missing. Install: '$CandidatePython' -m pip install -r '$ExampleRoot\requirements.txt'"
    }
    $env:TSP_EVALUATOR_PYTHON = $CandidatePython
    Write-Host "Candidate Python: $CandidatePython"

    if ($RegenerateData) {
        & $Python (Join-Path $RunRoot "generate_tsp_datasets.py")
        Assert-NativeSuccess "dataset generation"
    }

    Write-Step "2/8 Apply a small reproducible MEoH budget"
    & $Python (Join-Path $ExampleRoot "configure_task.py") `
        (Join-Path $RunRoot "config.yaml") `
        --population-size $PopulationSize `
        --max-samples $MaxSamples
    Assert-NativeSuccess "task configuration"

    $SpecPath = Join-Path $RunRoot "algorithm-design-spec.json"
    Write-Step "3/8 Compile the CSPaper review into AlgorithmDesignSpec"
    & $Llm4ad cspaper compile `
        --review (Join-Path $RunRoot "review.md") `
        --paper (Join-Path $RunRoot "paper.pdf") `
        --code-path (Join-Path $RunRoot "algorithm\ALNS-master") `
        --train-data (Join-Path $RunRoot "data\train") `
        --validation-data (Join-Path $RunRoot "data\validation") `
        --hidden-test-data (Join-Path $RunRoot "private-test") `
        --output $SpecPath
    Assert-NativeSuccess "cspaper compile"

    Write-Step "4/8 Validate and confirm the design specification"
    & $Llm4ad cspaper validate $SpecPath --check-paths
    Assert-NativeSuccess "cspaper validate"
    & $Llm4ad cspaper confirm $SpecPath `
        --by $ConfirmBy `
        --notes "Reproducible ALNS/TSP example inputs and evaluator contract verified."
    Assert-NativeSuccess "cspaper confirm"
    & $Llm4ad cspaper validate $SpecPath --strict --check-paths
    Assert-NativeSuccess "strict cspaper validate"

    Write-Step "5/8 Prepare the task and audit the evaluator contract"
    & $Llm4ad cspaper prepare --spec $SpecPath --task-dir $RunRoot
    Assert-NativeSuccess "cspaper prepare"

    Write-Step "6/8 Dry-run the baseline evaluator on train and validation data"
    & $Python (Join-Path $ExampleRoot "smoke_evaluator.py") `
        $RunRoot --include-validation `
        --output (Join-Path $RunRoot "dry-run-results.json")
    Assert-NativeSuccess "evaluator dry-run"

    if ($Evolve) {
        Write-Step "7/8 Check LLM credentials"
        if ([string]::IsNullOrWhiteSpace($env:LLM_BASE_URL) -and $env:AI_API_BASE_URL) {
            $env:LLM_BASE_URL = $env:AI_API_BASE_URL
        }
        if ([string]::IsNullOrWhiteSpace($env:LLM_API_KEY) -and $env:AI_API_KEY) {
            $env:LLM_API_KEY = $env:AI_API_KEY
        }
        if ([string]::IsNullOrWhiteSpace($env:LLM_MODEL) -and $env:AI_MODEL) {
            $env:LLM_MODEL = $env:AI_MODEL
        }
        $Missing = @("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") | Where-Object {
            [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
        }
        if ($Missing.Count -gt 0) {
            throw "Evolution needs environment variables: $($Missing -join ', ')"
        }

        Write-Step "8/8 Run MEoH evolution and export Top-K"
        & $Llm4ad cspaper evolve `
            --spec $SpecPath `
            --task-dir $RunRoot `
            --work-dir (Join-Path $RunRoot "pipeline") `
            --top-k $TopK
        Assert-NativeSuccess "cspaper evolve"
    }
    else {
        Write-Step "7-8/8 Evolution skipped; dry-run completed without LLM calls"
    }

    Write-Host ""
    Write-Host "Demo completed successfully." -ForegroundColor Green
    Write-Host "Run directory: $RunRoot"
    Write-Host "Spec: $SpecPath"
    Write-Host "Dry-run results: $(Join-Path $RunRoot 'dry-run-results.json')"
    Write-Host "Transcript: $TranscriptPath"
    $Succeeded = $true
}
catch {
    Write-Error $_
}
finally {
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}

if (-not $Succeeded) {
    exit 1
}
