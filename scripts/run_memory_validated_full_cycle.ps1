param(
    [string]$SparkHome = "$HOME\.spark-intelligence",
    [string]$OutputRoot = "",
    [int]$SoakRuns = 14,
    [double]$SoakTimeoutSeconds = 300,
    [switch]$SkipBaselinePublish,
    [switch]$PublishBaseline
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($SkipBaselinePublish -and $PublishBaseline) {
    throw "Do not pass both -SkipBaselinePublish and -PublishBaseline."
}

$scriptsRoot = $PSScriptRoot
$automationScript = Join-Path $scriptsRoot "run_memory_automation_tests.ps1"
$fullValidationScript = Join-Path $scriptsRoot "run_memory_two_contender_validation.ps1"

$hasCustomOutputRoot = -not [string]::IsNullOrWhiteSpace($OutputRoot)
$usesDefaultRunShape = (-not $hasCustomOutputRoot) -and ($SoakRuns -eq 14) -and ($SoakTimeoutSeconds -eq 300)
$effectiveSkipBaselinePublish = $SkipBaselinePublish -or (($PublishBaseline -eq $false) -and (-not $usesDefaultRunShape))

Write-Host "== Memory automation preflight =="
& powershell -NoProfile -ExecutionPolicy Bypass -File $automationScript
if ($LASTEXITCODE -ne 0) {
    throw "Memory automation preflight failed"
}

Write-Host ""
Write-Host "== Memory full validation =="
if ($effectiveSkipBaselinePublish) {
    Write-Host "Baseline publish mode: skipped"
} else {
    Write-Host "Baseline publish mode: enabled"
}

$fullArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $fullValidationScript,
    "-SparkHome", $SparkHome,
    "-SoakRuns", [string]$SoakRuns,
    "-SoakTimeoutSeconds", ([string]::Format("{0:0.###}", $SoakTimeoutSeconds))
)

if (-not [string]::IsNullOrWhiteSpace($OutputRoot)) {
    $fullArgs += @("-OutputRoot", $OutputRoot)
}
if ($effectiveSkipBaselinePublish) {
    $fullArgs += "-SkipBaselinePublish"
}

& powershell @fullArgs
if ($LASTEXITCODE -ne 0) {
    throw "Memory full validation failed"
}
