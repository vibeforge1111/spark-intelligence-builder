param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$builderRepoRoot = Split-Path -Parent $PSScriptRoot
$chipRepoRoot = Join-Path (Split-Path $builderRepoRoot -Parent) "domain-chip-memory"

Write-Host "== Builder memory automation tests =="
python -m pytest `
    tests/test_memory_baseline_doc_rendering.py `
    tests/test_memory_validation_artifact_rendering.py `
    tests/test_memory_validation_wrapper.py
if ($LASTEXITCODE -ne 0) {
    throw "Builder memory automation tests failed"
}

if (Test-Path $chipRepoRoot) {
    Write-Host ""
    Write-Host "== Chip memory automation tests =="
    Push-Location $chipRepoRoot
    try {
        python -m pytest tests/test_builder_baseline_doc_rendering.py
        if ($LASTEXITCODE -ne 0) {
            throw "Chip memory automation tests failed"
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host ""
    Write-Host "== Chip memory automation tests =="
    Write-Host "Skipped: sibling domain-chip-memory repo not found."
}
