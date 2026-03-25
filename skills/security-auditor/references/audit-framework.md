# Security Auditor Framework

## Use This For

- architecture review
- spec review
- diff review
- pre-implementation security grading
- hardening follow-up passes

## Audit Categories

Always inspect:

- identity and pairing
- session isolation
- provider and secret handling
- channel and webhook auth
- dangerous command approval
- tool and host execution boundaries
- process model and hidden background behavior
- logging and output leakage

## Severity Rules

- `critical`: cross-user leakage, host takeover path, auth bypass, secret exfil path
- `high`: dangerous default authority, missing replay/state verification, broken approval boundary
- `medium`: hardening gap likely to become exploitable or operationally unsafe
- `low`: clarity, observability, or revocation weakness

## Output Template

```text
Security Grade:
Critical Findings:
High Findings:
Medium Findings:
Low Findings:
Open Questions:
Hardening Priorities:
Required Tests:
```

## Grading Lens

Suggested mental grading:

- `A`: clear deny-by-default posture with explicit approvals and strong isolation
- `B`: sound architecture with a few contained hardening gaps
- `C`: useful but too trusting in one or two core boundaries
- `D`: multiple unsafe defaults or weak isolation
- `F`: serious host, auth, or cross-user risk
