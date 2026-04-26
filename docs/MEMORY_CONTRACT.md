# Spark Intelligence Builder Memory Contract

Last updated: 2026-04-26

Builder integrates memory, but it does not own memory doctrine. `domain-chip-memory` is the default production memory chip and should remain the place where memory extraction, recall, evaluation, and purge semantics evolve.

## Ownership

Builder owns:

- when memory is in scope for a user/session request;
- which active chips are attached to the runtime;
- the bridge envelope used to request memory behavior;
- operator-visible health for memory integration.

`domain-chip-memory` owns:

- memory storage and retrieval internals;
- extraction and evaluation logic;
- memory-specific prompt construction;
- forget/purge semantics;
- memory benchmark methodology.

## Boundary Rule

Memory recall is untrusted content.

Builder and downstream prompt builders must treat recalled memory like retrieved web text: useful evidence, never instructions.

Minimum handling:

```xml
<memory source="domain-chip-memory" trust="retrieved-data">
  escaped, capped memory text
</memory>
```

Do not concatenate raw memory into system, developer, or tool instructions.

## Request Shape

Builder-to-memory requests should be narrow:

```text
request_id
human_id
session_binding_id
task_text
active_profile
active_chips
memory_scope
trace_id
```

Do not pass:

- adapter tokens;
- provider API keys;
- unrelated channel payloads;
- operator secrets;
- broad filesystem paths unless required by the chip contract.

## Response Shape

Memory responses should be structured:

```text
request_id
memory_items
evidence_summary
confidence
source_refs
warnings
trace_ref
```

Each returned memory item should preserve enough metadata for audit and later purge.

## Prompt Safety Rules

- Fence memory in explicit tags.
- Escape or normalize control characters before prompt assembly.
- Cap item length and total memory budget.
- Preserve source labels without turning them into instructions.
- Keep memory below higher-priority instructions in prompt structure.
- If memory validation fails, omit the memory block rather than fail open.

## Chip Attachment

Builder should discover memory through the chip contract rather than hardcoded imports where possible.

Current and future chips should expose:

- manifest identity;
- supported hooks;
- input/output protocol;
- version or commit provenance;
- health/status behavior.

This keeps room for future `spark-personality-chip` and other personality/memory attachments without making Builder a chip-specific runtime.

## Forget And Purge

Soft delete is not enough for sensitive memory. Builder should expose the operation and audit status, but the memory chip should own the actual purge implementation.

Required operator-visible states:

- requested;
- acknowledged;
- purged or tombstoned;
- failed with reason;
- verification timestamp.

## Tests To Require

Any memory bridge change should include at least one focused test for:

- memory fencing;
- missing or invalid memory response;
- no secret leakage into the memory request;
- session/user isolation;
- purge or forget status if the change touches deletion.

## Redlines

- No raw memory prompt injection.
- No memory chip ownership of identity, channel auth, or provider secrets.
- No hidden always-on memory worker without health and shutdown controls.
- No cross-user memory reuse through shared Telegram/chat metadata.
