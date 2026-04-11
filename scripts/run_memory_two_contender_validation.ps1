param(
    [string]$SparkHome = "$HOME\.spark-intelligence",
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

$resolvedOutputRoot = $OutputRoot
if ([string]::IsNullOrWhiteSpace($resolvedOutputRoot)) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $resolvedOutputRoot = Join-Path $SparkHome "artifacts\memory-validation-runs\$timestamp"
}

New-Item -ItemType Directory -Path $resolvedOutputRoot -Force | Out-Null
Write-Host "Validation output root: $resolvedOutputRoot"

$runSummary = [ordered]@{
    output_root = $resolvedOutputRoot
    benchmark_output_dir = $null
    regression_output_dir = $null
    soak_output_dir = $null
}

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
        return (Join-Path $resolvedOutputRoot $LeafName)
    }
    return (Join-Path $resolvedOutputRoot $LeafName)
}

if (-not $SkipBenchmark) {
    $benchmarkArgs = @(
        "memory", "benchmark-architectures",
        "--home", $SparkHome
    ) + $baselineFlags
    $benchmarkOutputDir = Resolve-OutputDir -LeafName "memory-architecture-benchmark"
    if ($benchmarkOutputDir) {
        $benchmarkArgs += @("--output-dir", $benchmarkOutputDir)
        $runSummary["benchmark_output_dir"] = $benchmarkOutputDir
    }
    Invoke-ValidationStep -Label "Offline ProductMemory Benchmark" -Arguments $benchmarkArgs
}

if (-not $SkipRegression) {
    $regressionArgs = @(
        "memory", "run-telegram-regression",
        "--home", $SparkHome
    ) + $baselineFlags
    $regressionOutputDir = Resolve-OutputDir -LeafName "telegram-memory-regression"
    if ($regressionOutputDir) {
        $regressionArgs += @("--output-dir", $regressionOutputDir)
        $runSummary["regression_output_dir"] = $regressionOutputDir
    }
    Invoke-ValidationStep -Label "Live Telegram Regression" -Arguments $regressionArgs
}

if (-not $SkipSoak) {
    $soakArgs = @(
        "memory", "soak-architectures",
        "--home", $SparkHome,
        "--runs", [string]$SoakRuns,
        "--run-timeout-seconds", ([string]::Format("{0:0.###}", $SoakTimeoutSeconds))
    ) + $baselineFlags
    $soakOutputDir = Resolve-OutputDir -LeafName "telegram-memory-architecture-soak"
    if ($soakOutputDir) {
        $soakArgs += @("--output-dir", $soakOutputDir)
        $runSummary["soak_output_dir"] = $soakOutputDir
    }
    Invoke-ValidationStep -Label "Live Telegram Soak" -Arguments $soakArgs
}

$summaryPath = Join-Path $resolvedOutputRoot "run-summary.json"
$runSummary | ConvertTo-Json | Set-Content -Path $summaryPath -Encoding utf8

Write-Host ""
Write-Host "Validation artifacts:"
Write-Host ("- output root: " + $resolvedOutputRoot)
if ($runSummary["benchmark_output_dir"]) {
    Write-Host ("- offline benchmark: " + $runSummary["benchmark_output_dir"])
}
if ($runSummary["regression_output_dir"]) {
    Write-Host ("- live regression: " + $runSummary["regression_output_dir"])
}
if ($runSummary["soak_output_dir"]) {
    Write-Host ("- live soak: " + $runSummary["soak_output_dir"])
}
Write-Host ("- manifest: " + $summaryPath)
