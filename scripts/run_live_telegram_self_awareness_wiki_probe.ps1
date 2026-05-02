param(
    [string]$SparkHome = ".tmp-home-live-telegram-real",
    [int]$Limit = 80,
    [switch]$PrintPromptsOnly,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Prompts = @(
    "/self",
    "/wiki",
    "/wiki candidates",
    "/wiki scan-candidates",
    "What systems can you call, where do you lack, and how can you improve?",
    "What do you know about your memory system and what outranks wiki?",
    "What candidate wiki learnings need verification?",
    "Scan your wiki candidates for contradictions."
)

if ($PrintPromptsOnly) {
    Write-Host "Send these prompts to the live Spark Telegram bot, in order:"
    for ($i = 0; $i -lt $Prompts.Count; $i++) {
        Write-Host ("{0}. {1}" -f ($i + 1), $Prompts[$i])
    }
    return
}

$ExpectedTraces = @(
    @{
        Name = "slash self"
        BridgeMode = "runtime_command"
        RoutingDecision = "runtime_command"
        ResponseContains = @("Spark self-awareness")
    },
    @{
        Name = "slash wiki status"
        BridgeMode = "runtime_command"
        RoutingDecision = "runtime_command"
        ResponseContains = @("Spark LLM wiki status", "supporting_not_authoritative")
    },
    @{
        Name = "slash wiki candidates"
        BridgeMode = "runtime_command"
        RoutingDecision = "runtime_command"
        ResponseContains = @("Spark LLM wiki candidate inbox", "not live Spark truth")
    },
    @{
        Name = "slash wiki scan"
        BridgeMode = "runtime_command"
        RoutingDecision = "runtime_command"
        ResponseContains = @("Spark LLM wiki candidate contradiction scan", "not automatic promotion")
    },
    @{
        Name = "natural self awareness"
        BridgeMode = "self_awareness_direct"
        RoutingDecision = "self_awareness_direct"
        ResponseContains = @("Spark self-awareness", "Where I still lack")
    },
    @{
        Name = "natural memory cognition"
        BridgeMode = "self_awareness_direct"
        RoutingDecision = "self_awareness_direct"
        ResponseContains = @("Memory cognition", "current-state memory wins over wiki")
    },
    @{
        Name = "natural wiki candidate inbox"
        BridgeMode = "llm_wiki_candidate_inbox"
        RoutingDecision = "llm_wiki_candidate_inbox"
        ResponseContains = @("LLM wiki candidate inbox", "supporting_not_authoritative")
    },
    @{
        Name = "natural wiki candidate scan"
        BridgeMode = "llm_wiki_candidate_scan"
        RoutingDecision = "llm_wiki_candidate_scan"
        ResponseContains = @("LLM wiki candidate scan", "scan findings guide review")
    }
)

function Get-TraceField {
    param(
        [Parameter(Mandatory = $true)]$Trace,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $property = $Trace.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return ""
    }
    return [string]$property.Value
}

function Test-ContainsText {
    param(
        [Parameter(Mandatory = $true)][string]$Haystack,
        [Parameter(Mandatory = $true)][string]$Needle
    )

    return $Haystack.ToLowerInvariant().Contains($Needle.ToLowerInvariant())
}

function Test-ExpectedTrace {
    param(
        [Parameter(Mandatory = $true)]$Trace,
        [Parameter(Mandatory = $true)]$Expected
    )

    if ((Get-TraceField $Trace "bridge_mode") -ne $Expected.BridgeMode) {
        return $false
    }
    if ((Get-TraceField $Trace "routing_decision") -ne $Expected.RoutingDecision) {
        return $false
    }

    $response = Get-TraceField $Trace "response_preview"
    foreach ($needle in $Expected.ResponseContains) {
        if (-not (Test-ContainsText $response $needle)) {
            return $false
        }
    }

    return $true
}

$raw = & python -m spark_intelligence.cli gateway traces `
    --home $SparkHome `
    --channel-id telegram `
    --event telegram_update_processed `
    --limit $Limit `
    --json
if ($LASTEXITCODE -ne 0) {
    throw "gateway traces failed"
}

$jsonText = $raw -join [Environment]::NewLine
$parsedRows = $jsonText | ConvertFrom-Json
$rows = @($parsedRows | ForEach-Object { $_ })
$runtimeRows = @(
    $rows | Where-Object {
        $_.simulation -eq $false -and
        (Get-TraceField $_ "origin_surface") -eq "telegram_runtime" -and
        (Get-TraceField $_ "request_id").StartsWith("telegram:")
    }
)

$matched = New-Object System.Collections.Generic.List[object]
$searchStart = 0
foreach ($expected in $ExpectedTraces) {
    $matchIndex = -1
    for ($i = $searchStart; $i -lt $runtimeRows.Count; $i++) {
        if (Test-ExpectedTrace $runtimeRows[$i] $expected) {
            $matchIndex = $i
            break
        }
    }

    if ($matchIndex -lt 0) {
        $summary = [ordered]@{
            ok = $false
            spark_home = $SparkHome
            scanned_runtime_traces = $runtimeRows.Count
            matched = $matched.Count
            expected = $ExpectedTraces.Count
            missing = $expected.Name
        }
        if ($Json) {
            $summary | ConvertTo-Json -Depth 4
        } else {
            Write-Host ("FAIL matched {0}/{1} real Telegram runtime traces." -f $matched.Count, $ExpectedTraces.Count)
            Write-Host ("Missing expected trace: {0}" -f $expected.Name)
            Write-Host "Tip: run with -PrintPromptsOnly, send the prompts to the live bot, then rerun this verifier."
        }
        throw "live Telegram self-awareness/wiki probe failed"
    }

    $trace = $runtimeRows[$matchIndex]
    $matched.Add([ordered]@{
        name = $expected.Name
        recorded_at = Get-TraceField $trace "recorded_at"
        bridge_mode = Get-TraceField $trace "bridge_mode"
        routing_decision = Get-TraceField $trace "routing_decision"
        response_preview = Get-TraceField $trace "response_preview"
    })
    $searchStart = $matchIndex + 1
}

$result = [ordered]@{
    ok = $true
    spark_home = $SparkHome
    scanned_runtime_traces = $runtimeRows.Count
    matched = $matched.Count
    expected = $ExpectedTraces.Count
    traces = $matched
}

if ($Json) {
    $result | ConvertTo-Json -Depth 6
} else {
    Write-Host ("PASS matched {0}/{1} real Telegram self-awareness/wiki traces from {2}" -f $matched.Count, $ExpectedTraces.Count, $SparkHome)
    foreach ($item in $matched) {
        Write-Host ("PASS {0}: {1} / {2}" -f $item.name, $item.bridge_mode, $item.routing_decision)
    }
}
