# Spark Intelligence v1 Swarm Agent Operability Contract

## 1. Purpose

This document defines the minimum machine-readable operability surface an agent needs before it can safely run Spark Swarm workflows from Builder, Telegram, or another client.

The goal is to make questions like "can I run the loop", "can I push a win", and "can I open a PR" answerable through one structured diagnostic surface.

## 2. Core Rule

An agent should not infer operability from partial success.

It should check one normalized contract and act only on capabilities that are explicitly ready.

## 3. Design Goals

### 3.1 Client-Neutral Diagnostics

Telegram, CLI, and future runtimes should use the same readiness vocabulary.

### 3.2 Explicit GitHub Mode

Spark should say whether GitHub operations run through:

- Spark-managed GitHub app access
- local `gh` or git credentials
- no publish path

### 3.3 Actionable Failure States

The contract should name blockers and recommendations, not just return a boolean.

### 3.4 Safe Automation

Autoloops should only run when the required execution, sync, and publish surfaces are all visible.

## 4. Minimum Operability Envelope

Recommended v1 response shape:

```json
{
  "swarm": {
    "enabled": true,
    "configured": true,
    "auth_state": "configured",
    "auth_source": "workspace_env",
    "api_ready": true,
    "payload_ready": true
  },
  "specialization_path": {
    "active_path_key": "trading-crypto",
    "repo_root": "/abs/path/to/repo",
    "scenario_path": "/abs/path/to/scenario.json",
    "mutation_target_path": "/abs/path/to/target.json"
  },
  "github": {
    "publish_mode": "local_gh",
    "push_ready": true,
    "pr_ready": true,
    "merge_ready": false,
    "insight_publish_ready": true
  },
  "blockers": [],
  "recommendations": []
}
```

## 5. GitHub Capability Rules

### 5.1 Publish Modes

Recommended `publish_mode` values:

- `spark_github_app`
- `local_gh`
- `git_credential_helper`
- `none`

### 5.2 Push Ready

`push_ready` should only be true when Spark can verify at least one working path to push branch updates.

### 5.3 PR Ready

`pr_ready` should only be true when Spark can verify that a PR can be created through the active publish mode.

### 5.4 Merge Ready

`merge_ready` should be true only when the active publish mode also has merge authority.

Do not treat "can push" as "can merge".

### 5.5 Insight Publish Ready

`insight_publish_ready` should reflect whether benchmark wins or operator-approved outputs can be packaged and sent to the intended network destination.

## 6. Telegram Rule

Telegram should be able to surface this contract through a command such as `/swarm doctor`.

That command should give:

- a short operator summary
- a machine-readable payload for agents
- explicit blockers
- explicit next actions

## 7. Autoloop Rule

An autoloop should require:

- Swarm API ready
- specialization-path repo root resolved
- mutation target resolved
- path payload ready
- a known publish mode if the loop includes GitHub output

If any of those are missing, the loop should refuse to start and return the named blocker set.

## 8. Recommended Blocker Vocabulary

Recommended blocker keys:

```text
swarm_disabled
swarm_not_configured
swarm_auth_missing
swarm_api_unreachable
active_specialization_path_missing
specialization_repo_missing
specialization_contract_missing
scenario_path_missing
mutation_target_missing
collective_payload_missing
github_publish_unavailable
github_pr_unavailable
insight_publish_unavailable
```

## 9. Recommended Recommendation Vocabulary

Recommended recommendation keys:

```text
run_swarm_configure
attach_specialization_path_repo
add_specialization_path_json
configure_github_publish_mode
install_spark_github_app
login_local_gh
generate_collective_payload
```

## 10. v1 Practical Rule

For v1:

- keep the operability contract small
- make it directly consumable by agents
- expose GitHub publish mode explicitly
- let Telegram and CLI show the same readiness result

## 11. Final Decision

Spark Intelligence should expose one operability contract that tells any agent whether Spark Swarm, specialization-path execution, GitHub publication, and insight publication are actually ready.

That should replace guesswork with explicit capability checks.
