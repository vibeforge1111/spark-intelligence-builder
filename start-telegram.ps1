param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$SparkHome = ".tmp-home-live-telegram-real",
    [string]$PythonExe = "python",
    [switch]$Status,
    [switch]$SkipTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    param([Parameter(Mandatory = $true)][string]$RequestedCommand)

    $resolved = Get-Command $RequestedCommand -ErrorAction SilentlyContinue
    if ($resolved) {
        return $resolved.Source
    }

    $fallback = "C:\Python313\python.exe"
    if ($RequestedCommand -eq "python" -and (Test-Path -LiteralPath $fallback)) {
        return $fallback
    }

    throw "Python executable not found: $RequestedCommand"
}

function Invoke-SparkCli {
    param([Parameter(Mandatory = $true)][string[]]$CliArgs)

    $previousPyPath = $env:PYTHONPATH
    $previousSparkHome = $env:SPARK_INTELLIGENCE_HOME
    $srcPath = Join-Path $repoRoot "src"
    try {
        if ([string]::IsNullOrWhiteSpace($previousPyPath)) {
            $env:PYTHONPATH = $srcPath
        } else {
            $env:PYTHONPATH = "$srcPath$([System.IO.Path]::PathSeparator)$previousPyPath"
        }
        $env:SPARK_INTELLIGENCE_HOME = $homeWindows
        Push-Location $repoRoot
        & $pythonCommand -m spark_intelligence.cli @CliArgs
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
        $env:PYTHONPATH = $previousPyPath
        $env:SPARK_INTELLIGENCE_HOME = $previousSparkHome
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$homeWindows = if ([System.IO.Path]::IsPathRooted($SparkHome)) { $SparkHome } else { Join-Path $repoRoot $SparkHome }
$pythonCommand = Resolve-PythonCommand $PythonExe

if (-not (Test-Path -LiteralPath $homeWindows)) {
    throw "Spark Intelligence home not found: $homeWindows"
}

if ($Status) {
    $exitCode = Invoke-SparkCli @("channel", "test", "telegram")
    if ($exitCode -ne 0) {
        exit $exitCode
    }
    Write-Host "`n---"
    $exitCode = Invoke-SparkCli @("researcher", "status", "--json")
    if ($exitCode -ne 0) {
        exit $exitCode
    }
    Write-Host "`n---"
    exit (Invoke-SparkCli @("gateway", "status", "--json"))
}

if (-not $SkipTest) {
    Write-Host "Checking Telegram auth on $SparkHome ..."
    $exitCode = Invoke-SparkCli @("channel", "test", "telegram")
    if ($exitCode -ne 0) {
        throw "Telegram auth test failed. Gateway not started."
    }
}

Write-Host "Starting continuous Telegram gateway on $SparkHome ..."
exit (Invoke-SparkCli @("gateway", "start", "--continuous"))
