param(
    [string]$SparkHome = ".tmp-home-live-telegram-real",
    [int]$Limit = 80,
    [string]$OutputDir = "",
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
    "Scan your wiki candidates for contradictions.",
    "Review the quality of the /memory-quality build in spawner-ui.",
    "For later, Omar owns the launch checklist.",
    "Who owns the launch checklist?",
    "Why did you answer that way."
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
    },
    @{
        Name = "natural build quality review"
        BridgeMode = "build_quality_review_direct"
        RoutingDecision = "build_quality_review_direct"
        ResponseContains = @("Target repo: spawner-ui", "Tests:")
    },
    @{
        Name = "natural governed memory write"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        ResponseContains = @("I'll remember", "owned by Omar")
    },
    @{
        Name = "natural governed memory recall"
        BridgeMode = "memory_open_recall"
        RoutingDecision = "memory_open_recall_query"
        ResponseContains = @("Omar")
    },
    @{
        Name = "natural route explanation"
        BridgeMode = "context_source_debug"
        RoutingDecision = "context_source_debug"
        ResponseContains = @("Route:", "source:")
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

function Get-TraceDescriptor {
    param(
        $Trace
    )

    if ($null -eq $Trace) {
        return $null
    }

    return [ordered]@{
        recorded_at = Get-TraceField $Trace "recorded_at"
        simulation = Get-TraceField $Trace "simulation"
        origin_surface = Get-TraceField $Trace "origin_surface"
        request_id = Get-TraceField $Trace "request_id"
        bridge_mode = Get-TraceField $Trace "bridge_mode"
        routing_decision = Get-TraceField $Trace "routing_decision"
        user_message_preview = Get-TraceField $Trace "user_message_preview"
    }
}

function Get-TraceEligibilitySummary {
    param(
        [Parameter(Mandatory = $true)]$Rows,
        [Parameter(Mandatory = $true)]$RuntimeRows
    )

    $allRows = @($Rows)
    $eligibleRows = @($RuntimeRows)
    $simulationRows = @($allRows | Where-Object { $_.simulation -eq $true })
    $nonRuntimeSurfaceRows = @(
        $allRows | Where-Object {
            (Get-TraceField $_ "origin_surface") -ne "telegram_runtime"
        }
    )
    $nonTelegramRequestRows = @(
        $allRows | Where-Object {
            -not (Get-TraceField $_ "request_id").StartsWith("telegram:")
        }
    )
    $latestTrace = $null
    if ($allRows.Count -gt 0) {
        $latestTrace = $allRows[0]
    }
    $latestEligibleTrace = $null
    if ($eligibleRows.Count -gt 0) {
        $latestEligibleTrace = $eligibleRows[0]
    }

    return [ordered]@{
        scanned_traces = $allRows.Count
        eligible_runtime_traces = $eligibleRows.Count
        ignored_simulation_traces = $simulationRows.Count
        ignored_non_runtime_surface_traces = $nonRuntimeSurfaceRows.Count
        ignored_non_telegram_request_traces = $nonTelegramRequestRows.Count
        latest_trace = Get-TraceDescriptor $latestTrace
        latest_eligible_runtime_trace = Get-TraceDescriptor $latestEligibleTrace
    }
}

function Write-RegressionArtifact {
    param(
        [Parameter(Mandatory = $true)]$Payload
    )

    if ([string]::IsNullOrWhiteSpace($OutputDir)) {
        return
    }

    $artifactDir = New-Item -ItemType Directory -Path $OutputDir -Force
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $Payload.recorded_at = (Get-Date).ToUniversalTime().ToString("o")
    $jsonPayload = $Payload | ConvertTo-Json -Depth 8
    Set-Content -Path (Join-Path $artifactDir.FullName "latest.json") -Value $jsonPayload -Encoding UTF8
    Set-Content -Path (Join-Path $artifactDir.FullName ("live-telegram-self-awareness-wiki-{0}.json" -f $timestamp)) -Value $jsonPayload -Encoding UTF8
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
$traceEligibility = Get-TraceEligibilitySummary -Rows $rows -RuntimeRows $runtimeRows

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
            scanned_traces = $rows.Count
            scanned_runtime_traces = $runtimeRows.Count
            matched = $matched.Count
            expected = $ExpectedTraces.Count
            missing = $expected.Name
            trace_eligibility = $traceEligibility
            next_action = "Run with -PrintPromptsOnly, send the prompts to the live Spark Telegram bot in order, then rerun this verifier."
        }
        if ($Json) {
            $summary | ConvertTo-Json -Depth 4
        } else {
            Write-Host ("FAIL matched {0}/{1} real Telegram runtime traces." -f $matched.Count, $ExpectedTraces.Count)
            Write-Host ("Missing expected trace: {0}" -f $expected.Name)
            Write-Host ("Scanned {0} trace(s), {1} eligible live runtime trace(s), ignored {2} simulation trace(s)." -f $rows.Count, $runtimeRows.Count, $traceEligibility.ignored_simulation_traces)
            Write-Host "Tip: run with -PrintPromptsOnly, send the prompts to the live bot, then rerun this verifier."
        }
        Write-RegressionArtifact -Payload $summary
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
    scanned_traces = $rows.Count
    scanned_runtime_traces = $runtimeRows.Count
    matched = $matched.Count
    expected = $ExpectedTraces.Count
    trace_eligibility = $traceEligibility
    traces = $matched
}

Write-RegressionArtifact -Payload $result

if ($Json) {
    $result | ConvertTo-Json -Depth 6
} else {
    Write-Host ("PASS matched {0}/{1} real Telegram self-awareness/wiki traces from {2}" -f $matched.Count, $ExpectedTraces.Count, $SparkHome)
    foreach ($item in $matched) {
        Write-Host ("PASS {0}: {1} / {2}" -f $item.name, $item.bridge_mode, $item.routing_decision)
    }
}
