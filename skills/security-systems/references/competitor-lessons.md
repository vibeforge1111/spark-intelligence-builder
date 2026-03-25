# Competitor Security Lessons

## OpenClaw Public Signals

Public hardening signals visible in the repo include:

- inherited host env sanitization hardening
- webhook replay dedupe hardening
- OAuth state hardening
- gateway auth fixes
- session lock and pairing hardening

Concrete examples:

- `fix(exec): harden inherited host env sanitization (#25755)`
- `fix(voice-call): harden Telnyx replay dedupe (#25832)`
- `fix(security): harden chutes manual oauth state check (#16058)`

Useful branch signals:

- `security-9792-sanitize-host-base-env-clean`
- `nextcloud-webhook-auth-before-body-20260224`
- `session-lock-timeout-recovery`
- `fix/security-audit-gateway-auth`

Design lesson:

- sanitize authority crossing into the host
- treat webhooks as hostile
- fail closed on auth and state validation

## Hermes Public Signals

Public hardening signals visible in the repo include:

- file permission hardening
- unauthorized DM handling
- DM and group session isolation
- dangerous command approval clarity
- gateway/platform hardening

Concrete examples:

- `security: enforce 0600/0700 file permissions on sensitive files`
- `feat: support ignoring unauthorized gateway DMs`
- `test: cover DM session key isolation`
- `feat: add 'View full command' option to dangerous command approval`

Useful branch signals:

- `feat/file-permissions-hardening`
- `feat/unauthorized-dm-behavior`
- `fix/1056-dm-session-isolation`
- `fix/814-group-session-isolation`
- `fix/gateway-platform-hardening`
- `fix-leakage`

Design lesson:

- local files need real permission hardening
- DMs and sessions need explicit isolation
- approval UX matters because vague approval text is a security bug

## Spark Takeaways

- secure identity before adding more channels
- secure command approval before adding more tool power
- secure webhook verification before adding more adapters
- secure files and secrets before adding more config surface

For the deeper comparative history analysis, also read:

- `docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md`
