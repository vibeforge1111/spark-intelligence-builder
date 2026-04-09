# Telegram Command Reference 2026-04-09

This is the current Builder-owned Telegram control surface.

The rule is:

- explicit slash commands stay the canonical operator interface
- bounded natural-language equivalents are supported where the intent is clear
- natural-language capture should map onto the same internal command path, not create a second behavior system

## Swarm

- `/swarm`
- `/swarm status`
- `/swarm overview`
- `/swarm live`
- `/swarm runtime`
- `/swarm specializations`
- `/swarm insights [specialization]`
- `/swarm masteries [specialization]`
- `/swarm upgrades [specialization]`
- `/swarm issues`
- `/swarm inbox`
- `/swarm collective`
- `/swarm sync`
- `/swarm paths`
- `/swarm run <path_key>`
- `/swarm autoloop <path_key> [rounds <n>]`
- `/swarm continue <path_key> [session <id>] [rounds <n>]`
- `/swarm sessions <path_key>`
- `/swarm session <path_key> [latest|<session_id>]`
- `/swarm rerun [path_key]`
- `/swarm evaluate <task>`
- `/swarm absorb <insight_id> [because <reason>]`
- `/swarm review <mastery_id> <approve|defer|reject> [because <reason>]`
- `/swarm mode <specialization_id> <observe_only|review_required|checked_auto_merge|trusted_auto_apply>`
- `/swarm deliver <upgrade_id>`
- `/swarm sync-delivery <upgrade_id>`

Natural-language examples:

- `Can you show me the swarm status?`
- `What upgrades are pending in swarm?`
- `Please sync with swarm`
- `Can you evaluate this for swarm: <task>`
- `Show me Startup Operator insights in swarm`
- `Start autoloop for Startup Operator in swarm for 2 rounds`

## Style

- `/style`
- `/style status`
- `/style history`
- `/style score`
- `/style compare`
- `/style test`
- `/style train <instruction>`
- `/style feedback <note>`
- `/style good <note>`
- `/style bad <note>`

Natural-language examples:

- `Can you show me my current style?`
- `What style changes have you saved?`
- `Score my style`
- `Compare my style`
- `Train your style to be more direct and keep replies short`
- `Be more Claude-like in conversation continuity`
- `That was too verbose`
- `Less canned and more grounded follow-up questions`

Current natural-language style capture is intentionally bounded to explicit style/personality requests and clear reply-quality feedback. It should not try to reinterpret arbitrary conversation as style mutation.

## Voice

- `/voice`
- `/voice plan`
- `/voice reply`
- `/voice reply status`
- `/voice reply on`
- `/voice reply off`
- `/voice speak <text>`

Natural-language examples:

- `What is the voice status?`
- `How does voice work?`
- `Turn voice replies on`
- `Turn voice replies off`
- `Please speak this out loud: <text>`
- `Send this as voice: <text>`

## Think Visibility

- `/think`
- `/think on`
- `/think off`

Natural-language examples:

- `What is the thinking status?`
- `Turn thinking on`
- `Turn thinking off`

## Implementation Rule

When adding a new Telegram runtime command:

1. Add the explicit slash command first.
2. Add bounded natural-language equivalents only when intent can be recognized reliably.
3. Route natural-language capture into the same internal command handler.
4. Add simulation tests for both slash and natural-language forms.
5. Update this file and the README command section in the same change.
