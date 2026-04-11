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

function Get-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }
    return Get-Content $Path -Raw | ConvertFrom-Json
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

$benchmarkSummaryPath = if ($runSummary["benchmark_output_dir"]) { Join-Path $runSummary["benchmark_output_dir"] "memory-architecture-benchmark.json" } else { $null }
$regressionSummaryPath = if ($runSummary["regression_output_dir"]) { Join-Path $runSummary["regression_output_dir"] "telegram-memory-regression.json" } else { $null }
$soakSummaryPath = if ($runSummary["soak_output_dir"]) { Join-Path $runSummary["soak_output_dir"] "telegram-memory-architecture-soak.json" } else { $null }

$benchmarkPayload = if ($benchmarkSummaryPath) { Get-JsonFile -Path $benchmarkSummaryPath } else { $null }
$regressionPayload = if ($regressionSummaryPath) { Get-JsonFile -Path $regressionSummaryPath } else { $null }
$soakPayload = if ($soakSummaryPath) { Get-JsonFile -Path $soakSummaryPath } else { $null }

Write-Host ""
Write-Host "Validation verdict:"
if ($benchmarkPayload) {
    $benchmarkSummary = $benchmarkPayload.summary
    Write-Host ("- offline runtime architecture: " + $benchmarkSummary.runtime_memory_architecture)
    Write-Host ("- offline ProductMemory leaders: " + (($benchmarkSummary.product_memory_leader_names | ForEach-Object { $_ }) -join ", "))
}
if ($regressionPayload) {
    $regressionSummary = $regressionPayload.summary
    Write-Host ("- live regression: " + $regressionSummary.matched_case_count + "/" + $regressionSummary.case_count)
    Write-Host ("- live regression leaders: " + (($regressionSummary.live_architecture_leaders | ForEach-Object { $_ }) -join ", "))
}
if ($soakPayload) {
    $soakSummary = $soakPayload.summary
    Write-Host ("- live soak: " + $soakSummary.completed_runs + "/" + $soakSummary.requested_runs + " completed")
    Write-Host ("- live soak leaders: " + (($soakSummary.overall_leader_names | ForEach-Object { $_ }) -join ", "))
    Write-Host ("- live soak recommended top two: " + (($soakSummary.recommended_top_two | ForEach-Object { $_ }) -join ", "))
}
