param(
    [string]$SparkHome = "$HOME\.spark-intelligence",
    [string]$OutputRoot = "",
    [int]$SoakRuns = 14,
    [double]$SoakTimeoutSeconds = 300,
    [switch]$SkipBaselinePublish
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptsRoot = $PSScriptRoot
$automationScript = Join-Path $scriptsRoot "run_memory_automation_tests.ps1"
$fullValidationScript = Join-Path $scriptsRoot "run_memory_two_contender_validation.ps1"

Write-Host "== Memory automation preflight =="
& powershell -NoProfile -ExecutionPolicy Bypass -File $automationScript
if ($LASTEXITCODE -ne 0) {
    throw "Memory automation preflight failed"
}

Write-Host ""
Write-Host "== Memory full validation =="

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
if ($SkipBaselinePublish) {
    $fullArgs += "-SkipBaselinePublish"
}

& powershell @fullArgs
if ($LASTEXITCODE -ne 0) {
    throw "Memory full validation failed"
}
