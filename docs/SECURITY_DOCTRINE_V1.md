# Spark Intelligence v1 Security Doctrine

## 1. Purpose

This document defines the security doctrine for `Spark Intelligence`.

It exists so new features do not slowly weaken the product.

The goal is to make a local AI agent that is:

- useful
- inspectable
- secure by default
- difficult to accidentally over-authorize

## 2. Core Security Rule

Every new feature must answer:

- what authority does this add
- who gets that authority
- how is it constrained
- how is it revoked
- how is it inspected

If the answer is vague, the feature is not ready.

## 3. Security Principles

### 3.1 Deny By Default

Unknown users, unknown channels, unknown tools, and unknown surfaces do not get full access.

### 3.2 Least Authority

Give the minimum authority needed for the feature to work.

### 3.3 One Security Truth Surface

Identity, pairing, allowlists, and approval state must not be split across competing subsystems.

### 3.4 Fail Closed

If auth, pairing, verification, or validation fails, stop the action.

### 3.5 Local Inspectability

An operator should be able to inspect:

- who is paired
- which sessions exist
- which tools are enabled
- which provider and channel auth is active
- what dangerous action was requested

without reverse-engineering hidden state.

## 4. Critical Security Boundaries

### 4.1 Identity Boundary

One user must never inherit another user's context.

Guardrails:

- explicit pairing
- explicit cross-channel linking
- no username-only linking
- no shared-room assumptions by default

### 4.2 Host Boundary

The agent must not silently gain broad control over the host.

Guardrails:

- explicit tool permission gates
- explicit dangerous-action approvals
- minimal inherited environment
- no silent shell escalation

### 4.3 Secrets Boundary

Secrets must be treated as toxic assets.

Guardrails:

- no secret logging
- no secret duplication
- strict file permissions
- clear rotation path

### 4.4 Session Boundary

Sessions define continuity and therefore risk.

Guardrails:

- DM isolation
- group isolation
- session revocation
- explicit session binding per surface

### 4.5 Webhook And Adapter Boundary

External webhook and messaging inputs are hostile until proven valid.

Guardrails:

- auth before expensive processing where possible
- replay protection
- scope checks
- input normalization

## 5. Guardrails We Should Encode Early

### 5.1 Identity And Pairing

- allowlist or explicit pairing required
- no automatic group access
- no auto-linking across channels without proof
- auditable pairing approvals and revocations

### 5.2 Dangerous Commands

- show the full command, not a vague summary
- require explicit approval for high-risk host actions
- preserve approval logs

### 5.3 Filesystem And Secrets

- config secrets separated from general config where practical
- sensitive files with strict permissions
- no world-readable token stores

### 5.4 Runtime And Process Model

- no homegrown daemon managers
- no hidden watchdog loops
- no detached child-process tricks as the reliability foundation

### 5.5 Logging And Output

- no secret leakage
- no accidental cross-user transcript leakage
- no unsafe debug dumps in normal paths

## 6. Public Hardening Signals From OpenClaw

Current public repo signals show OpenClaw repeatedly hardening:

- inherited host environment sanitization
- webhook replay handling
- OAuth state checks
- gateway auth behavior
- session and pairing behavior

Concrete public examples from the repo:

- `fix(exec): harden inherited host env sanitization (#25755)`
- `fix(voice-call): harden Telnyx replay dedupe (#25832)`
- `fix(security): harden chutes manual oauth state check (#16058)`

Additional branch signals:

- `security-9792-sanitize-host-base-env-clean`
- `nextcloud-webhook-auth-before-body-20260224`
- `session-lock-timeout-recovery`
- `whatsapp-login-guard-security`
- `fix/oc-25-oauth-csrf-state-fabrication`

Source: public OpenClaw repo and refs at `https://github.com/openclaw/openclaw`.

## 7. Public Hardening Signals From Hermes

Current public repo signals show Hermes repeatedly hardening:

- sensitive file permissions
- unauthorized DM behavior
- DM and group session isolation
- dangerous command approvals
- gateway/platform hardening

Concrete public examples from the repo:

- `security: enforce 0600/0700 file permissions on sensitive files`
- `feat: support ignoring unauthorized gateway DMs`
- `test: cover DM session key isolation`
- `feat: add 'View full command' option to dangerous command approval`

Additional branch signals:

- `feat/file-permissions-hardening`
- `feat/unauthorized-dm-behavior`
- `fix/1056-dm-session-isolation`
- `fix/814-group-session-isolation`
- `fix/gateway-platform-hardening`
- `fix-leakage`

Source: public Hermes repo and refs at `https://github.com/NousResearch/hermes-agent`.

## 8. What We Should Do Better From Day One

Spark should start stricter in these places:

- one canonical identity store
- explicit DMs-first posture
- explicit cross-channel linking proof
- explicit full-command approvals
- strict secret-file permissions
- reduced host env inheritance
- explicit webhook replay and state validation
- no daemon sprawl

## 9. Security Review Questions

Before shipping a feature, ask:

1. Does this create a new trust boundary?
2. Does it widen host authority?
3. Does it create a new identity or session path?
4. Does it expose secrets or tokens?
5. Does it rely on hidden background behavior?
6. Does it fail closed?
7. Can the operator inspect and revoke it?

## 10. Final Rule

Spark Intelligence should behave like a cautious system that must earn trust.

A convenient but unsafe shortcut is not a product advantage.
