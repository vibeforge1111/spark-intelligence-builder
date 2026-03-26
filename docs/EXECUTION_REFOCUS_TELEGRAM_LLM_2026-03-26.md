# Spark Intelligence Execution Refocus 2026-03-26

## 1. Why The Plan Changed

The repo now has a real Telegram runtime and a live Telegram home, but that same live home still has no provider configured.

That means the next highest-signal work is not Discord hardening.

The next highest-signal work is making the real Telegram plus LLM path trustworthy end to end.

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

- the live Telegram home does not yet have a configured provider
- Codex auth has not yet been validated on that real Telegram home
- the real gateway has not yet been re-run with Codex or another provider on the live home
- the exact operator recovery flow for "Telegram is healthy but provider execution is blocked" should be proven in practice, not just in tests

## 4. Revised Execution Order

Use this order from here:

1. finish the remaining phase-1 runtime/operator cleanup only when it affects the Telegram plus LLM path directly
2. validate Codex auth on the real Telegram home
3. prove one real Telegram plus provider execution path end to end
4. tighten Telegram plus LLM recovery and rotation flows until they are boring
5. only then resume Discord live validation
6. leave broader WhatsApp work after that

## 5. Immediate Telegram Plus LLM Target

The immediate target is:

- live Telegram home
- one configured provider
- `auth status`, `gateway status`, and `status` all consistent
- `gateway start --once` healthy on the same home
- one real Telegram reply path using the configured provider/runtime contract

## 6. Immediate Next Commands

The next practical steps are:

1. start Codex OAuth on the real Telegram home
2. complete the browser auth flow
3. re-run:
   - `spark-intelligence auth status`
   - `spark-intelligence gateway status`
   - `spark-intelligence status`
4. validate `gateway start --once` on that same home

## 7. Explicit Deferral

For now, do not spend the next slice on:

- broader Discord runtime behavior
- WhatsApp message-type breadth
- guild Discord flows
- component or modal flows
- adapter breadth for its own sake
