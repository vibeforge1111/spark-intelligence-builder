# Spark Intelligence Security Research Plan

## 1. Purpose

This document defines the research plan for a later deep security review of:

- Hermes Agent
- OpenClaw

The goal is not superficial comparison.

The goal is to understand where they:

- strengthened security over time
- patched trust-boundary issues
- improved permission handling
- tightened pairing, identity, and adapter safety
- fixed local runtime or host-control risks

and then make Spark Intelligence safer from the start.

## 2. Why This Matters

Persistent local agents can have:

- access to the filesystem
- access to local tools
- access to browser or platform sessions
- access to model-provider credentials
- long-lived runtime state

That makes security architecture a product-critical concern, not a polish pass.

## 3. Research Scope

The later deep pass should inspect:

- commit history
- PR history
- release notes
- issue threads
- security docs
- permission and pairing flows
- auth and secret handling
- host-execution guardrails

## 4. Main Research Questions

### 4.1 Trust Boundaries

- How do Hermes and OpenClaw separate:
  - inbound user identity
  - runtime authority
  - tool authority
  - operator authority

### 4.2 Pairing And Access Control

- How did they tighten allowlists, pairing, and first-user claim behavior over time?

### 4.3 Tool And Host Safety

- How do they prevent a local agent from doing too much by default?
- What later patches reveal earlier trust mistakes?

### 4.4 Secrets And Credentials

- How do they store and validate:
  - adapter tokens
  - model keys
  - OAuth credentials

### 4.5 Group And Shared Context Safety

- How do they prevent cross-user leakage in:
  - Discord servers
  - Telegram groups
  - WhatsApp chats

### 4.6 Runtime Hardening

- What did they change to reduce:
  - orphan processes
  - watchdog loops
  - daemon instability
  - hidden background behavior

## 5. Deliverables For The Later Security Pass

The later deep pass should produce:

- one comparative security review doc
- one commit-pattern findings doc
- one Spark security doctrine doc
- one prioritized remediation matrix

## 6. Spark Questions To Answer

By the end of the research, we should be able to answer:

1. Where should Spark be stricter than both from day one?
2. Which defaults should be deny-by-default?
3. Which local permissions should require explicit escalation?
4. Which host actions should be blocked entirely in v1?
5. Which identity and pairing patterns are too risky to allow early?

## 7. Recommended Spark Security Themes

Even before the deep pass, Spark should bias toward:

- deny-by-default identity linking
- explicit allowlists and pairing
- one canonical identity store
- explicit tool permission gates
- no hidden daemon or watchdog layers
- local inspectability for auth, state, and active sessions

## 8. Final Rule

Do not wait for a security incident to discover the real architecture.

The later Hermes/OpenClaw security research should be used to harden Spark Intelligence before feature sprawl makes change expensive.
