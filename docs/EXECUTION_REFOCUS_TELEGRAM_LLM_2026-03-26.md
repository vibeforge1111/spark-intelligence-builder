# Spark Intelligence Execution Refocus 2026-03-26

## 1. Why The Plan Changed

The repo now has a real Telegram runtime and a live Telegram home, and that same live home has now been proven with a real provider-backed reply path.

That means the next highest-signal work is still not Discord hardening.

The next highest-signal work is making the real Telegram plus LLM path boring, recoverable, and repeatable.

## 2. What Is Already Done

These areas are already materially real:

- Telegram onboarding, pairing, moderation, and live validation
- provider registry and auth-profile persistence
- API-key and OAuth coexistence
- gateway-owned OAuth callback flow
- fail-closed provider runtime and execution readiness
- operator-visible auth repair guidance
- direct provider execution for API-key-backed providers
- explicit wrapper-backed Codex policy for v1
- narrow Discord and WhatsApp ingress contracts

## 3. What Is Still Missing

These gaps matter more than broader adapter work right now:

- the canonical live home should be treated as MiniMax-backed first, not half-configured for multiple provider experiments
- Codex auth has not yet been validated on that real Telegram home and is currently blocked by upstream auth errors
- the exact operator recovery flow for "Telegram is healthy but provider runtime or execution is blocked" should still be proven in practice, not just in tests
- token rotation, provider rotation, and provider failure recovery should be rechecked on the same canonical home now that MiniMax is live

## 4. Revised Execution Order

Use this order from here:

1. finish the remaining phase-1 runtime/operator cleanup only when it affects the Telegram plus LLM path directly
2. keep the real Telegram home narrowed to one working provider path
3. tighten Telegram plus LLM recovery and rotation flows until they are boring
4. decide whether Codex auth should be retried immediately or deferred behind the proven MiniMax path
5. only then resume Discord live validation
6. leave broader WhatsApp work after that

## 5. Immediate Telegram Plus LLM Target

The immediate target is:

- live Telegram home
- one configured provider
- `auth status`, `gateway status`, and `status` all consistent
- `gateway start --once` healthy on the same home
- one real Telegram reply path using the configured provider/runtime contract

That target is now reached on the canonical home:

- home: `.tmp-home-live-telegram-real`
- channel: Telegram
- provider: `custom` via MiniMax
- model: `MiniMax-M2.7`
- base URL: `https://api.minimax.io/v1`
- live result: one real inbound Telegram DM processed and one outbound reply sent successfully

## 6. Immediate Next Commands

The next practical steps are:

1. re-run the canonical live Telegram path after any token, provider, or pairing change:
   - `spark-intelligence auth status`
   - `spark-intelligence gateway status`
   - `spark-intelligence status`
   - `spark-intelligence gateway start --once`
2. prove the operator recovery path for provider failure on that same home:
   - break provider readiness intentionally in a controlled way
   - confirm `doctor`, `status`, `gateway status`, `gateway start`, and `operator security` point at the right repair path
3. prove one more live Telegram reply after the repair
4. only then decide whether to retry Codex OAuth on the same home or defer it behind the stable MiniMax path

## 7. Codex Status

Codex OAuth is not the blocking path for Telegram anymore.

The live Telegram plus LLM path is already proven through MiniMax on the canonical home.

Codex OAuth remains valuable, but it is currently a deferred provider-auth problem rather than a blocker for the core Telegram vertical slice.

## 8. Explicit Deferral

For now, do not spend the next slice on:

- broader Discord runtime behavior
- WhatsApp message-type breadth
- guild Discord flows
- component or modal flows
- adapter breadth for its own sake
