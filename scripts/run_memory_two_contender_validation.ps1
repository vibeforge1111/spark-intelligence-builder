param(
    [string]$Home = "$HOME\.spark-intelligence",
    [string]$OutputRoot = "",
    [int]$SoakRuns = 14,
    [double]$SoakTimeoutSeconds = 300,
    [switch]$SkipBenchmark,
    [switch]$SkipRegression,
    [switch]$SkipSoak
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$baselineFlags = @(
    "--baseline", "summary_synthesis_memory",
    "--baseline", "dual_store_event_calendar_hybrid"
)

function Invoke-ValidationStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host ""
    Write-Host "== $Label =="
    Write-Host ("spark-intelligence " + ($Arguments -join " "))
    & spark-intelligence @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Validation step failed: $Label"
    }
}

function Resolve-OutputDir {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LeafName
    )

    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        return $null
    }
    return (Join-Path $OutputRoot $LeafName)
}

if (-not $SkipBenchmark) {
    $benchmarkArgs = @(
        "memory", "benchmark-architectures",
        "--home", $Home
    ) + $baselineFlags
    $benchmarkOutputDir = Resolve-OutputDir -LeafName "memory-architecture-benchmark"
    if ($benchmarkOutputDir) {
        $benchmarkArgs += @("--output-dir", $benchmarkOutputDir)
    }
    Invoke-ValidationStep -Label "Offline ProductMemory Benchmark" -Arguments $benchmarkArgs
}

if (-not $SkipRegression) {
    $regressionArgs = @(
        "memory", "run-telegram-regression",
        "--home", $Home
    ) + $baselineFlags
    $regressionOutputDir = Resolve-OutputDir -LeafName "telegram-memory-regression"
    if ($regressionOutputDir) {
        $regressionArgs += @("--output-dir", $regressionOutputDir)
    }
    Invoke-ValidationStep -Label "Live Telegram Regression" -Arguments $regressionArgs
}

if (-not $SkipSoak) {
    $soakArgs = @(
        "memory", "soak-architectures",
        "--home", $Home,
        "--runs", [string]$SoakRuns,
        "--run-timeout-seconds", ([string]::Format("{0:0.###}", $SoakTimeoutSeconds))
    ) + $baselineFlags
    $soakOutputDir = Resolve-OutputDir -LeafName "telegram-memory-architecture-soak"
    if ($soakOutputDir) {
        $soakArgs += @("--output-dir", $soakOutputDir)
    }
    Invoke-ValidationStep -Label "Live Telegram Soak" -Arguments $soakArgs
}
