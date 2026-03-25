# Competitor Findings For Audits

Use these as reminder classes of mistakes to check for.

## OpenClaw

Public repo signals suggest repeated hardening around:

- host env sanitization
- webhook replay protection
- OAuth state validation
- gateway auth probes
- session lock recovery

Audit implication:

- ask whether Spark inherits too much host authority
- ask whether webhook/state verification is fail-closed
- ask whether auth probes accidentally use the wrong trust source

## Hermes

Public repo signals suggest repeated hardening around:

- sensitive file permissions
- unauthorized DM handling
- session isolation
- dangerous-command approval UX
- general leakage and platform hardening

Audit implication:

- ask whether local files are too open
- ask whether unknown DMs can do too much
- ask whether sessions can collide or leak across surfaces
- ask whether command approvals hide important detail

## Spark Audit Rule

If Hermes or OpenClaw had to harden a class of issue later, assume Spark should review that class before shipping.

For the deeper comparative history analysis, also read:

- `docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md`
