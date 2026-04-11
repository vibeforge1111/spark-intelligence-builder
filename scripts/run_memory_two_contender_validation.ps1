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

$expectedLatestFullRunPointer = Join-Path $SparkHome "artifacts\memory-validation-runs\latest-full-run.json"

function Get-ExpectedValidationBaseline {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LatestFullRunPath
    )

    if (-not (Test-Path $LatestFullRunPath)) {
        return $null
    }

    try {
        $pointerPayload = Get-Content $LatestFullRunPath -Raw | ConvertFrom-Json
        $runSummaryPath = [string]$pointerPayload.run_summary
        if ([string]::IsNullOrWhiteSpace($runSummaryPath) -or -not (Test-Path $runSummaryPath)) {
            return $null
        }
        $runSummaryPayload = Get-Content $runSummaryPath -Raw | ConvertFrom-Json
        return [ordered]@{
            output_root = [string]$pointerPayload.output_root
            builder_repo_commit = $runSummaryPayload.builder_repo_commit
            domain_chip_repo_commit = $runSummaryPayload.domain_chip_repo_commit
            benchmark_duration_seconds = $runSummaryPayload.benchmark_duration_seconds
            regression_duration_seconds = $runSummaryPayload.regression_duration_seconds
            soak_duration_seconds = $runSummaryPayload.soak_duration_seconds
            total_duration_seconds = $runSummaryPayload.total_duration_seconds
        }
    } catch {
        return $null
    }
}

function Format-ExpectedDuration {
    param(
        [Parameter(Mandatory = $false)]
        [object]$Value
    )

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return "unknown"
    }
    return ("{0:0.###}s" -f [double]$Value)
}

$resolvedOutputRoot = $OutputRoot
if ([string]::IsNullOrWhiteSpace($resolvedOutputRoot)) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $resolvedOutputRoot = Join-Path $SparkHome "artifacts\memory-validation-runs\$timestamp"
}

New-Item -ItemType Directory -Path $resolvedOutputRoot -Force | Out-Null

function Get-GitRevision {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
        return $null
    }
    $revision = git -C $RepoRoot rev-parse HEAD 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($revision | Select-Object -First 1)
}

function Invoke-LedgerRender {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$LatestRunPath,
        [Parameter(Mandatory = $true)]
        [string]$LedgerPath
    )

    $renderScript = Join-Path $RepoRoot "scripts\render_memory_failure_ledger.py"
    if (-not (Test-Path $renderScript)) {
        return $false
    }
    python $renderScript --latest-run $LatestRunPath --write $LedgerPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to render memory failure ledger"
    }
    return $true
}

function Invoke-BaselineDocsRender {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$LatestRunPath
    )

    $renderScript = Join-Path $RepoRoot "scripts\render_memory_baseline_docs.py"
    if (-not (Test-Path $renderScript)) {
        return $false
    }
    python $renderScript --latest-run $LatestRunPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to render memory baseline docs"
    }
    return $true
}

function Test-IsFullValidationSummary {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RunSummaryPath
    )

    if (-not (Test-Path $RunSummaryPath)) {
        return $false
    }
    $payload = Get-Content $RunSummaryPath -Raw | ConvertFrom-Json
    return (
        -not [string]::IsNullOrWhiteSpace([string]$payload.benchmark_output_dir) -and
        -not [string]::IsNullOrWhiteSpace([string]$payload.regression_output_dir) -and
        -not [string]::IsNullOrWhiteSpace([string]$payload.soak_output_dir)
    )
}

function Ensure-LatestFullRunPointer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ValidationRunsRoot,
        [Parameter(Mandatory = $true)]
        [string]$LatestFullRunPath
    )

    if (Test-Path $LatestFullRunPath) {
        return
    }
    $candidateDirs = Get-ChildItem -Path $ValidationRunsRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    foreach ($dir in $candidateDirs) {
        $summaryPath = Join-Path $dir.FullName "run-summary.json"
        if (-not (Test-IsFullValidationSummary -RunSummaryPath $summaryPath)) {
            continue
        }
        [ordered]@{
            output_root = $dir.FullName
            run_summary = $summaryPath
            updated_at = (Get-Date).ToString("o")
        } | ConvertTo-Json | Set-Content -Path $LatestFullRunPath -Encoding utf8
        return
    }
}

$builderRepoRoot = (Get-Location).Path
$domainChipRepoRoot = Join-Path (Split-Path $builderRepoRoot -Parent) "domain-chip-memory"
$builderRevision = Get-GitRevision -RepoRoot $builderRepoRoot
$domainChipRevision = Get-GitRevision -RepoRoot $domainChipRepoRoot
$expectedBaseline = Get-ExpectedValidationBaseline -LatestFullRunPath $expectedLatestFullRunPointer
Write-Host "Validation output root: $resolvedOutputRoot"
Write-Host "Expected full-run cost from latest clean baseline:"
if ($expectedBaseline) {
    Write-Host ("- source baseline: " + $expectedBaseline.output_root)
    Write-Host ("- benchmark: " + (Format-ExpectedDuration -Value $expectedBaseline.benchmark_duration_seconds))
    Write-Host ("- regression: " + (Format-ExpectedDuration -Value $expectedBaseline.regression_duration_seconds))
    Write-Host ("- soak: " + (Format-ExpectedDuration -Value $expectedBaseline.soak_duration_seconds))
    Write-Host ("- total: " + (Format-ExpectedDuration -Value $expectedBaseline.total_duration_seconds))
    if (
        (($expectedBaseline.builder_repo_commit) -and ($builderRevision) -and ([string]$expectedBaseline.builder_repo_commit -ne [string]$builderRevision)) -or
        (($expectedBaseline.domain_chip_repo_commit) -and ($domainChipRevision) -and ([string]$expectedBaseline.domain_chip_repo_commit -ne [string]$domainChipRevision))
    ) {
        Write-Host "- baseline staleness: warning"
        if (($expectedBaseline.builder_repo_commit) -and ($builderRevision) -and ([string]$expectedBaseline.builder_repo_commit -ne [string]$builderRevision)) {
            Write-Host ("  builder baseline commit: " + [string]$expectedBaseline.builder_repo_commit)
            Write-Host ("  builder current commit: " + [string]$builderRevision)
        }
        if (($expectedBaseline.domain_chip_repo_commit) -and ($domainChipRevision) -and ([string]$expectedBaseline.domain_chip_repo_commit -ne [string]$domainChipRevision)) {
            Write-Host ("  domain-chip baseline commit: " + [string]$expectedBaseline.domain_chip_repo_commit)
            Write-Host ("  domain-chip current commit: " + [string]$domainChipRevision)
        }
    } else {
        Write-Host "- baseline staleness: clean"
    }
} else {
    Write-Host "- source baseline: unavailable"
    Write-Host "- benchmark: unknown"
    Write-Host "- regression: unknown"
    Write-Host "- soak: unknown"
    Write-Host "- total: unknown"
    Write-Host "- baseline staleness: unknown"
}
Write-Host ("- latest full-run pointer: " + $expectedLatestFullRunPointer)

$runStartedAt = Get-Date

$runSummary = [ordered]@{
    started_at = $runStartedAt.ToString("o")
    finished_at = $null
    total_duration_seconds = $null
    spark_home = $SparkHome
    output_root = $resolvedOutputRoot
    builder_repo_root = $builderRepoRoot
    builder_repo_commit = $builderRevision
    domain_chip_repo_root = $(if (Test-Path $domainChipRepoRoot) { $domainChipRepoRoot } else { $null })
    domain_chip_repo_commit = $domainChipRevision
    baselines = @("summary_synthesis_memory", "dual_store_event_calendar_hybrid")
    soak_runs = $SoakRuns
    soak_timeout_seconds = $SoakTimeoutSeconds
    skipped_steps = @()
    benchmark_duration_seconds = $null
    regression_duration_seconds = $null
    soak_duration_seconds = $null
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
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    & spark-intelligence @Arguments | Out-Host
    $stopwatch.Stop()
    if ($LASTEXITCODE -ne 0) {
        throw "Validation step failed: $Label"
    }
    return [math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
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
    $runSummary["benchmark_duration_seconds"] = Invoke-ValidationStep -Label "Offline ProductMemory Benchmark" -Arguments $benchmarkArgs
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
    $runSummary["regression_duration_seconds"] = Invoke-ValidationStep -Label "Live Telegram Regression" -Arguments $regressionArgs
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
    $runSummary["soak_duration_seconds"] = Invoke-ValidationStep -Label "Live Telegram Soak" -Arguments $soakArgs
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

$runFinishedAt = Get-Date
$runSummary["finished_at"] = $runFinishedAt.ToString("o")
$runSummary["total_duration_seconds"] = [math]::Round(($runFinishedAt - $runStartedAt).TotalSeconds, 3)

$summaryPath = Join-Path $resolvedOutputRoot "run-summary.json"
$runSummary | ConvertTo-Json | Set-Content -Path $summaryPath -Encoding utf8

$validationRunsRoot = Join-Path $SparkHome "artifacts\memory-validation-runs"
$latestRunPath = Join-Path $validationRunsRoot "latest-run.json"
$latestFullRunPath = Join-Path $validationRunsRoot "latest-full-run.json"
$previousFullRunPath = Join-Path $validationRunsRoot "previous-full-run.json"
[ordered]@{
    output_root = $resolvedOutputRoot
    run_summary = $summaryPath
    updated_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Path $latestRunPath -Encoding utf8
Ensure-LatestFullRunPointer -ValidationRunsRoot $validationRunsRoot -LatestFullRunPath $latestFullRunPath

$ledgerPath = Join-Path $builderRepoRoot "docs\MEMORY_FAILURE_LEDGER_2026-04-11.md"
$deltaPath = Join-Path $resolvedOutputRoot "validation-delta.md"
$canRenderLedger = (
    -not [string]::IsNullOrWhiteSpace([string]$runSummary["benchmark_output_dir"]) -and
    -not [string]::IsNullOrWhiteSpace([string]$runSummary["regression_output_dir"]) -and
    -not [string]::IsNullOrWhiteSpace([string]$runSummary["soak_output_dir"])
)
$ledgerRendered = $false
$baselineDocsRendered = $false
$deltaRendered = $false
if ($canRenderLedger) {
    $priorFullRunJson = if (Test-Path $latestFullRunPath) { Get-Content $latestFullRunPath -Raw } else { $null }
    if ($priorFullRunJson) {
        Set-Content -Path $previousFullRunPath -Value $priorFullRunJson -Encoding utf8
    }
    [ordered]@{
        output_root = $resolvedOutputRoot
        run_summary = $summaryPath
        updated_at = (Get-Date).ToString("o")
    } | ConvertTo-Json | Set-Content -Path $latestFullRunPath -Encoding utf8
    $ledgerRendered = Invoke-LedgerRender -RepoRoot $builderRepoRoot -LatestRunPath $latestFullRunPath -LedgerPath $ledgerPath
    $baselineDocsRendered = Invoke-BaselineDocsRender -RepoRoot $builderRepoRoot -LatestRunPath $latestFullRunPath
    $deltaScript = Join-Path $builderRepoRoot "scripts\render_memory_validation_delta.py"
    if ((Test-Path $deltaScript) -and (Test-Path $previousFullRunPath)) {
        python $deltaScript --latest $latestFullRunPath --previous $previousFullRunPath --write $deltaPath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to render memory validation delta"
        }
        $deltaRendered = $true
    }
}

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
if (Test-Path $latestFullRunPath) {
    Write-Host ("- latest full-run pointer: " + $latestFullRunPath)
}
if (Test-Path $previousFullRunPath) {
    Write-Host ("- previous full-run pointer: " + $previousFullRunPath)
}
if ($ledgerRendered) {
    Write-Host ("- failure ledger: " + $ledgerPath)
}
if ($baselineDocsRendered) {
    Write-Host ("- baseline docs: " + (Join-Path $builderRepoRoot "README.md"))
    Write-Host ("- baseline handoff docs: " + (Join-Path $builderRepoRoot "docs\MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md"))
    Write-Host ("- baseline benchmark handoff: " + (Join-Path $builderRepoRoot "docs\MEMORY_BENCHMARK_HANDOFF_2026-04-11.md"))
}
if ($deltaRendered) {
    Write-Host ("- validation delta: " + $deltaPath)
}

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
Write-Host ("- benchmark duration seconds: " + $(if ($null -ne $runSummary["benchmark_duration_seconds"]) { $runSummary["benchmark_duration_seconds"] } else { "skipped" }))
Write-Host ("- regression duration seconds: " + $(if ($null -ne $runSummary["regression_duration_seconds"]) { $runSummary["regression_duration_seconds"] } else { "skipped" }))
Write-Host ("- soak duration seconds: " + $(if ($null -ne $runSummary["soak_duration_seconds"]) { $runSummary["soak_duration_seconds"] } else { "skipped" }))
Write-Host ("- total duration seconds: " + $runSummary["total_duration_seconds"])
