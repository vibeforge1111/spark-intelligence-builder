param(
    [string]$SparkHome = ".tmp-home-live-telegram-real",
    [int]$Limit = 80,
    [switch]$PrintPromptsOnly,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Prompts = @(
    "my favorite color is bright green",
    "The food I love the most is shakshuka.",
    "What is my favorite food?",
    "Forget my favorite food.",
    "What is my favorite food?",
    "We plan to complete live memory checks.",
    "Actually, the current plan is to complete live memory preference checks.",
    "What is my current plan?",
    "What was my previous plan?",
    "Forget my current plan.",
    "What is my current plan?",
    "We committed to closing the pilot by June 1.",
    "Actually, our current commitment is to finish the memory Telegram checks tonight.",
    "What is our commitment?",
    "What was our previous commitment?",
    "Forget our commitment.",
    "What is our commitment?"
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
        Name = "favorite color write"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        EvidenceContains = @("profile.favorite_color")
        ResponseContains = @("favorite color is bright green")
    },
    @{
        Name = "favorite food write"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        EvidenceContains = @("profile.favorite_food")
        ResponseContains = @("favorite food is shakshuka")
    },
    @{
        Name = "favorite food current query"
        BridgeMode = "memory_profile_fact"
        RoutingDecision = "memory_profile_fact_query"
        EvidenceContains = @("profile.favorite_food", "value_found=yes")
        ResponseContains = @("favorite food is shakshuka")
    },
    @{
        Name = "favorite food delete"
        BridgeMode = "memory_generic_observation_delete"
        RoutingDecision = "memory_generic_observation_delete"
        EvidenceContains = @("profile.favorite_food")
        ResponseContains = @("forget your favorite food")
    },
    @{
        Name = "favorite food post-delete query"
        BridgeMode = "memory_profile_fact"
        RoutingDecision = "memory_profile_fact_query"
        EvidenceContains = @("profile.favorite_food", "value_found=no")
        ResponseContains = @("currently have that saved")
    },
    @{
        Name = "current plan initial write"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        EvidenceContains = @("profile.current_plan")
        ResponseContains = @("complete live memory checks")
    },
    @{
        Name = "current plan overwrite"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        EvidenceContains = @("profile.current_plan")
        ResponseContains = @("complete live memory preference checks")
    },
    @{
        Name = "current plan current query"
        BridgeMode = "memory_profile_fact"
        RoutingDecision = "memory_profile_fact_query"
        EvidenceContains = @("profile.current_plan", "value_found=yes")
        ResponseContains = @("complete live memory preference checks")
    },
    @{
        Name = "current plan previous query"
        BridgeMode = "memory_profile_fact_history"
        RoutingDecision = "memory_profile_fact_history_query"
        EvidenceContains = @("profile.current_plan", "previous_value_found=yes")
        ResponseContains = @("Before your current plan", "complete live memory checks")
    },
    @{
        Name = "current plan delete"
        BridgeMode = "memory_generic_observation_delete"
        RoutingDecision = "memory_generic_observation_delete"
        EvidenceContains = @("profile.current_plan")
        ResponseContains = @("forget your current plan")
    },
    @{
        Name = "current plan post-delete query"
        BridgeMode = "memory_profile_fact"
        RoutingDecision = "memory_profile_fact_query"
        EvidenceContains = @("profile.current_plan", "value_found=no")
        ResponseContains = @("currently have that saved")
    },
    @{
        Name = "current commitment initial write"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        EvidenceContains = @("profile.current_commitment")
        ResponseContains = @("closing the pilot by June 1")
    },
    @{
        Name = "current commitment overwrite"
        BridgeMode = "memory_generic_observation_update"
        RoutingDecision = "memory_generic_observation"
        EvidenceContains = @("profile.current_commitment")
        ResponseContains = @("finish the memory Telegram checks tonight")
    },
    @{
        Name = "current commitment current query"
        BridgeMode = "memory_profile_fact"
        RoutingDecision = "memory_profile_fact_query"
        EvidenceContains = @("profile.current_commitment", "value_found=yes")
        ResponseContains = @("finish the memory Telegram checks tonight")
    },
    @{
        Name = "current commitment previous query"
        BridgeMode = "memory_profile_fact_history"
        RoutingDecision = "memory_profile_fact_history_query"
        EvidenceContains = @("profile.current_commitment", "previous_value_found=yes")
        ResponseContains = @("Before your current commitment", "closing the pilot by June 1")
    },
    @{
        Name = "current commitment delete"
        BridgeMode = "memory_generic_observation_delete"
        RoutingDecision = "memory_generic_observation_delete"
        EvidenceContains = @("profile.current_commitment")
        ResponseContains = @("forget your current commitment")
    },
    @{
        Name = "current commitment post-delete query"
        BridgeMode = "memory_profile_fact"
        RoutingDecision = "memory_profile_fact_query"
        EvidenceContains = @("profile.current_commitment", "value_found=no")
        ResponseContains = @("currently have that saved")
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

    $evidence = Get-TraceField $Trace "evidence_summary"
    foreach ($needle in $Expected.EvidenceContains) {
        if (-not (Test-ContainsText $evidence $needle)) {
            return $false
        }
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
        throw "live Telegram memory probe failed"
    }

    $trace = $runtimeRows[$matchIndex]
    $matched.Add([ordered]@{
        name = $expected.Name
        recorded_at = Get-TraceField $trace "recorded_at"
        bridge_mode = Get-TraceField $trace "bridge_mode"
        routing_decision = Get-TraceField $trace "routing_decision"
        evidence_summary = Get-TraceField $trace "evidence_summary"
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
    Write-Host ("PASS matched {0}/{1} real Telegram runtime traces from {2}" -f $matched.Count, $ExpectedTraces.Count, $SparkHome)
    foreach ($item in $matched) {
        Write-Host ("PASS {0}: {1} / {2}" -f $item.name, $item.bridge_mode, $item.routing_decision)
    }
}
