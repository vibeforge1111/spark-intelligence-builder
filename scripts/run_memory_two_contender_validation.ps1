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
    spark_home = $SparkHome
    output_root = $resolvedOutputRoot
    baselines = @("summary_synthesis_memory", "dual_store_event_calendar_hybrid")
    soak_runs = $SoakRuns
    soak_timeout_seconds = $SoakTimeoutSeconds
    skipped_steps = @()
    benchmark_output_dir = $null
    regression_output_dir = $null
    soak_output_dir = $null
    offline_runtime_architecture = $null
    offline_product_memory_leaders = @()
    live_regression = $null
    live_regression_leaders = @()
    live_soak_completion = $null
    live_soak_leaders = @()
    live_soak_recommended_top_two = @()
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
} else {
    $runSummary["skipped_steps"] += "benchmark"
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
} else {
    $runSummary["skipped_steps"] += "regression"
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
} else {
    $runSummary["skipped_steps"] += "soak"
}

$benchmarkSummaryPath = if ($runSummary["benchmark_output_dir"]) { Join-Path $runSummary["benchmark_output_dir"] "memory-architecture-benchmark.json" } else { $null }
$regressionSummaryPath = if ($runSummary["regression_output_dir"]) { Join-Path $runSummary["regression_output_dir"] "telegram-memory-regression.json" } else { $null }
$soakSummaryPath = if ($runSummary["soak_output_dir"]) { Join-Path $runSummary["soak_output_dir"] "telegram-memory-architecture-soak.json" } else { $null }

$benchmarkPayload = if ($benchmarkSummaryPath) { Get-JsonFile -Path $benchmarkSummaryPath } else { $null }
$regressionPayload = if ($regressionSummaryPath) { Get-JsonFile -Path $regressionSummaryPath } else { $null }
$soakPayload = if ($soakSummaryPath) { Get-JsonFile -Path $soakSummaryPath } else { $null }

if ($benchmarkPayload) {
    $benchmarkSummary = $benchmarkPayload.summary
    $runSummary["offline_runtime_architecture"] = $benchmarkSummary.runtime_memory_architecture
    $runSummary["offline_product_memory_leaders"] = @($benchmarkSummary.product_memory_leader_names)
}
if ($regressionPayload) {
    $regressionSummary = $regressionPayload.summary
    $runSummary["live_regression"] = "$($regressionSummary.matched_case_count)/$($regressionSummary.case_count)"
    $runSummary["live_regression_leaders"] = @($regressionSummary.live_architecture_leaders)
}
if ($soakPayload) {
    $soakSummary = $soakPayload.summary
    $runSummary["live_soak_completion"] = "$($soakSummary.completed_runs)/$($soakSummary.requested_runs)"
    $runSummary["live_soak_leaders"] = @($soakSummary.overall_leader_names)
    $runSummary["live_soak_recommended_top_two"] = @($soakSummary.recommended_top_two)
}

$summaryPath = Join-Path $resolvedOutputRoot "run-summary.json"
$runSummary | ConvertTo-Json | Set-Content -Path $summaryPath -Encoding utf8

$latestRunPath = Join-Path (Join-Path $SparkHome "artifacts\memory-validation-runs") "latest-run.json"
[ordered]@{
    output_root = $resolvedOutputRoot
    run_summary = $summaryPath
    updated_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Path $latestRunPath -Encoding utf8

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
Write-Host ("- latest pointer: " + $latestRunPath)

Write-Host ""
Write-Host "Validation verdict:"
if ($benchmarkPayload) {
    Write-Host ("- offline runtime architecture: " + $benchmarkSummary.runtime_memory_architecture)
    Write-Host ("- offline ProductMemory leaders: " + (($benchmarkSummary.product_memory_leader_names | ForEach-Object { $_ }) -join ", "))
}
if ($regressionPayload) {
    Write-Host ("- live regression: " + $regressionSummary.matched_case_count + "/" + $regressionSummary.case_count)
    Write-Host ("- live regression leaders: " + (($regressionSummary.live_architecture_leaders | ForEach-Object { $_ }) -join ", "))
}
if ($soakPayload) {
    Write-Host ("- live soak: " + $soakSummary.completed_runs + "/" + $soakSummary.requested_runs + " completed")
    Write-Host ("- live soak leaders: " + (($soakSummary.overall_leader_names | ForEach-Object { $_ }) -join ", "))
    Write-Host ("- live soak recommended top two: " + (($soakSummary.recommended_top_two | ForEach-Object { $_ }) -join ", "))
}
