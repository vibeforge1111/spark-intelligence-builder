# OpenClaw And Hermes Security History Analysis 2026-03-25

## 1. Purpose

This document records a broad security-history analysis of:

- OpenClaw
- Hermes Agent

The goal is to understand which classes of security and hardening work kept recurring in their histories, so Spark Intelligence can start stricter in those places instead of rediscovering them later.

## 2. Method

This analysis used two layers:

### 2.1 Full-History Mining

I scanned commit history metadata across all refs for both public repos:

- OpenClaw: `25,579` commits across refs
- Hermes: `3,098` commits across refs

I then keyword-clustered commit subjects into rough security themes:

- identity and pairing
- auth, OAuth, tokens, webhooks, replay
- host and tool boundaries
- leakage, redaction, secret exposure
- sessions, locks, and continuity

These counts are useful directional signals, not perfect truth, because commit messages are noisy.

### 2.2 Representative Diff Inspection

I also inspected concrete public commits and branch tips that were clearly security-relevant.

Examples inspected:

- OpenClaw
  - `fix(exec): harden inherited host env sanitization (#25755)`
  - `fix(voice-call): harden Telnyx replay dedupe (#25832)`
  - `fix(security): harden chutes manual oauth state check (#16058)`
  - `fix(security): block command substitution in unquoted heredoc bodies`
  - `fix: allow token auth without device identity (#1314)`

- Hermes
  - `security: enforce 0600/0700 file permissions on sensitive files`
  - `feat: support ignoring unauthorized gateway DMs`
  - `test: cover DM session key isolation`
  - `feat: add 'View full command' option to dangerous command approval`

## 3. High-Level Read

The important pattern is not that either system was insecure overall.

The important pattern is that the same classes of problems kept demanding hardening:

- identity confusion
- unauthorized direct-message behavior
- session collision or leakage
- weak or incomplete approval surfaces
- host authority leaking through env or exec paths
- webhook and OAuth validation edge cases
- secret and token leakage
- growing operational complexity around long-lived runtimes

Spark should assume those classes will matter from day one.

## 4. Full-History Signals

### 4.1 OpenClaw

Approximate keyword-cluster signals from full history mining:

- auth and webhook related: `~1600`
- runtime and session related: `~1581`
- host and tool boundary related: `~1070`
- identity and pairing related: `~1053`
- leakage and logging related: `~314`

Interpretation:

- OpenClaw has a broad attack surface and a broad hardening surface
- messaging channels, auth paths, and long-lived sessions repeatedly needed tightening
- host and execution boundaries also kept resurfacing

### 4.2 Hermes

Approximate keyword-cluster signals from full history mining:

- runtime and session related: `~243`
- auth and webhook related: `~116`
- host and tool boundary related: `~114`
- identity and pairing related: `~58`
- leakage and logging related: `~37`

Interpretation:

- Hermes has a smaller and cleaner hardening surface
- its security churn clusters more tightly around session isolation, local host safety, and clean authorization behavior

## 5. Thematic Findings

## 5.1 Identity, Pairing, And Unauthorized DMs

### OpenClaw Signals

Public signals repeatedly referenced:

- pairing replies
- pairing codes
- DM peer or direct-peer handling
- allowlists and policy clarification
- gateway token auth behavior

Representative visible refs and commits:

- `fix: scope Telegram pairing code blocks (#52784)`
- `telegram-dm-direct-peer`
- `zalo-pairing-and-webhook`
- `discord-log-allowlist-ignored`
- `allow token auth without device identity`

### Hermes Signals

Public signals repeatedly referenced:

- unauthorized DM behavior
- DM session isolation
- group session isolation
- allowlist env coverage

Representative visible refs and commits:

- `feat: support ignoring unauthorized gateway DMs`
- `test: cover DM session key isolation`
- `fix/814-group-session-isolation`
- `fix/gateway): add all missing platform allowlist env vars to startup warning check`

### Spark Rule

Spark should do all of this from the start:

- DM-first
- explicit pairing
- explicit cross-channel linking
- explicit allowlists
- no shared-room assumptions by default
- no new sender silently inheriting an existing agent

## 5.2 Sessions, Locks, And Continuity

### OpenClaw Signals

OpenClaw had many session and continuity hardening signals:

- deterministic transcript session keys
- session reset safety
- admin requirement for session reset
- session lock timeout recovery
- thread routing preservation after restart

Representative visible refs and commits:

- `fix: prefer deterministic transcript session keys`
- `fix(gateway): require admin for agent session reset`
- `session-lock-timeout-recovery`
- `fix(gateway): restart sentinel wakes session after restart and preserves thread routing`

### Hermes Signals

Hermes showed a smaller but clear pattern:

- DM session key isolation
- group session isolation
- session reset correctness
- thread/session handling fixes

Representative visible refs and commits:

- `test: cover DM session key isolation`
- `fix(gateway): make group session isolation configurable`
- `fix: complete session reset — missing compressor counters + test`
- `Slack thread handling — progress messages, responses, and session isolation`

### Spark Rule

Spark should treat session state as a security-critical continuity boundary:

- one canonical session store
- one session binding per surface
- admin-only or operator-approved resets where destructive
- no adapter-local session truth

## 5.3 Host Authority, Tool Safety, And Command Approval

### OpenClaw Signals

OpenClaw’s history shows repeated effort around execution trust:

- inherited host env sanitization
- exec policy and wrapper trust
- media root bypass closure
- heredoc command substitution blocking
- sandbox visibility and trust semantics

Representative visible refs and commits:

- `fix(exec): harden inherited host env sanitization (#25755)`
- `fix: close sandbox media root bypass for mediaUrl/fileUrl aliases (#54034)`
- `fix: split exec and policy resolution for wrapper trust (#53134)`
- `fix(security): block command substitution in unquoted heredoc bodies`

### Hermes Signals

Hermes showed explicit local-host hardening:

- file-permission hardening
- dangerous-command approval clarity
- sandbox fail-closed thinking
- SQL safety in execute calls

Representative visible refs and commits:

- `security: enforce 0600/0700 file permissions on sensitive files`
- `feat: add 'View full command' option to dangerous command approval`
- `fix(security): eliminate SQL string formatting in execute() calls`
- `pr-10-sandbox-exec-fail-closed` branch signal

### Spark Rule

Spark should start with:

- sanitized inherited environment
- strict tool permission gates
- full-command dangerous-action approvals
- 0600/0700 sensitive file permissions
- fail-closed execution paths

## 5.4 Webhooks, Tokens, OAuth, And Replay

### OpenClaw Signals

OpenClaw repeatedly hardened:

- webhook auth order
- replay dedupe
- OAuth state checks
- token auth behavior
- auth mode preservation

Representative visible refs and commits:

- `fix(voice-call): harden Telnyx replay dedupe (#25832)`
- `nextcloud-webhook-auth-before-body-20260224`
- `fix(security): harden chutes manual oauth state check (#16058)`
- `fix: use local auth for gateway probe (#1011)`

### Hermes Signals

Hermes repeatedly hardened:

- OAuth path traversal and shared-state issues
- provider auth behavior
- token and startup auth checks
- platform auth configuration correctness

Representative visible refs and commits:

- `fix(mcp-oauth): port mismatch, path traversal, and shared handler state`
- `fix(auth): preserve 'custom' provider instead of silently remapping to 'openrouter'`
- `fix: recognize Claude Code OAuth credentials in startup gate`

### Spark Rule

Spark should enforce:

- replay protection
- explicit token scope checks
- explicit OAuth state verification
- fail-closed startup when auth is invalid
- no silent provider remapping

## 5.5 Leakage, Redaction, And Secret Handling

### OpenClaw Signals

OpenClaw visible hardening signals included:

- sensitive config redaction
- SecretRef handling fixes
- avoiding crashes on unresolved secret references

Representative visible refs and commits:

- `fix(ui): redact sensitive config values in diff panel`
- `fix(secrets): prevent unresolved SecretRef from crashing embedded agent runs`
- `fix: command auth SecretRef resolution`

### Hermes Signals

Hermes visible hardening signals included:

- token leakage prevention
- redaction robustness
- gateway YAML PII redaction
- unavailable tool names leaking into schemas

Representative visible refs and commits:

- `fix: prevent Anthropic token leaking to third-party anthropic_messages providers`
- `fix(redact): safely handle non-string inputs`
- `fix: prevent unavailable tool names from leaking into model schemas`

### Spark Rule

Spark should:

- never log raw tokens
- never silently weaken redaction behavior
- separate secret handling from general config
- fail clearly when secret resolution is broken

## 5.6 Operational Complexity And Runtime Shape

### OpenClaw Read

OpenClaw’s hardening surface is broad partly because the runtime and adapter surface is broad.

That does not make it wrong, but it does mean more moving parts create more places to harden:

- daemon identity
- gateway auth probes
- session restart behavior
- webhook auth order
- pairing behavior across many surfaces

### Hermes Read

Hermes appears to benefit from a smaller, cleaner shape:

- fewer surfaces
- cleaner CLI rhythm
- explicit file-permission hardening
- simpler local boundaries

### Spark Rule

Spark should stay closer to the Hermes complexity budget, while borrowing OpenClaw’s paranoia on auth, session, and webhook edges.

That means:

- foreground-first runtime
- native autostart wrappers only when needed
- no homegrown daemon manager
- one canonical identity/session store
- small adapter budget

## 6. Where Spark Should Be Better Than Both

Spark should be stricter from day one in these places:

1. One canonical identity store with explicit cross-channel linking proof
2. DMs-first and deny-by-default for new senders
3. Full-command dangerous approvals, never vague summaries only
4. Sanitized inherited host env by default
5. Strict sensitive-file permissions
6. Replay and OAuth-state validation as first-class requirements
7. No hidden daemon, watchdog, or detached-child-process security story
8. Explicit operator visibility for pairing, sessions, auth, and approvals

## 7. What This Means For The Skills

The security skills should keep checking these classes on every new feature:

- identity confusion
- session leakage
- webhook and OAuth edge validation
- host and tool authority widening
- command-approval ambiguity
- secret leakage
- daemon or process opacity

## 8. Limits

This analysis scanned full public commit history metadata and inspected representative public diffs.

It did not read every diff line of every commit.

So the correct claim is:

- broad full-history mining
- targeted representative diff inspection

not:

- complete line-by-line audit of every historical change

For the compact theme summary extracted from the full-history mining, also read:

- `docs/SECURITY_HISTORY_THEME_APPENDIX_2026-03-25.md`

## 9. Final Conclusion

OpenClaw teaches that wide agent systems repeatedly need hardening around auth, webhooks, sessions, and host boundaries.

Hermes teaches that a smaller local-agent system still needs serious hardening around file permissions, unauthorized DMs, session isolation, and dangerous-command UX.

Spark should take:

- Hermes simplicity
- OpenClaw edge paranoia

and combine them into a smaller, stricter local-agent architecture from the start.
