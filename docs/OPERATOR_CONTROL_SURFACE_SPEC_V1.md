# Spark Intelligence v1 Operator Control Surface Spec

## 1. Purpose

This document defines the v1 operator control surface for `Spark Intelligence`.

The goal is to make runtime control:

- visible
- inspectable
- safe
- separate from ordinary chat authority

## 2. Core Rule

Paired chat access is not operator authority.

A user being allowed to message the agent must not automatically grant permission to:

- change runtime config
- mutate pairings or allowlists
- restart adapters or runtime services
- change dangerous-action approvals
- alter delivery or session policies

## 3. Roles

### 3.1 Paired User

Can:

- message the agent
- continue approved sessions
- request work within granted tool authority

Cannot:

- mutate global control-plane policy
- approve other users
- alter runtime ownership

### 3.2 Owner

Can:

- inspect the workspace
- approve and revoke pairings
- inspect sessions
- inspect config and health

### 3.3 Operator Admin

Can:

- perform owner actions
- change config
- start or stop adapters through the official control path
- install or remove autostart wrappers
- approve high-risk control-plane changes

v1 may collapse `owner` and `operator.admin` into one local operator identity if implementation simplicity requires it.

But the logical distinction should remain in the design.

## 4. Surface Areas

The control surface should initially exist in the CLI.

Recommended commands:

- `spark-intelligence agent inspect`
- `spark-intelligence pairings list`
- `spark-intelligence pairings approve`
- `spark-intelligence pairings revoke`
- `spark-intelligence sessions list`
- `spark-intelligence sessions revoke`
- `spark-intelligence channel status`
- `spark-intelligence channel restart <channel>`
- `spark-intelligence doctor`
- `spark-intelligence gateway status`

## 5. Runtime Ownership Rule

The runtime should only be managed through the operator control surface.

Do not let normal agent output or tool calls:

- shell out into background runtime restarts
- install hidden watchdogs
- rewrite service metadata directly

If runtime control is needed, route it through explicit operator actions.

## 6. Required Visibility

The operator should be able to inspect:

- active adapters
- current health state
- paired users
- session bindings
- current provider
- dangerous approval policy
- last job failures

## 7. Safe Mutation Rules

Any control-surface mutation should be:

- explicit
- logged
- targeted
- reversible where practical

High-risk examples:

- pairings approval
- allowlist mutation
- runtime restart
- provider switch
- home-channel change

## 8. Anti-Patterns To Reject

- chat commands that mutate core policy without role checks
- hidden operator powers embedded in adapter command handlers
- separate admin truth living inside one adapter
- agent-generated shell instructions being treated as authorized control-plane changes

## 9. Final Rule

Spark Intelligence should have one control plane, and it should belong to the operator, not to ordinary chat traffic.
