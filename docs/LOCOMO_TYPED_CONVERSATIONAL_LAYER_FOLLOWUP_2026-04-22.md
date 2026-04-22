# LoCoMo Typed Conversational Layer Followup 2026-04-22

## Scope

This follow-up measures the same answer-present unseen LoCoMo slice used in:

- [LOCOMO_UNSEEN_SLICE_FOLLOWUP_2026-04-22.md](/C:/Users/USER/Desktop/spark-intelligence-builder/docs/LOCOMO_UNSEEN_SLICE_FOLLOWUP_2026-04-22.md)

Current substrate checkpoints:

- domain commit: `8ad9431`
  - `Add conversational memory layer plan`
- domain commit: `689cc0a`
  - `Add typed conversational temporal memory layer`

Measured lane:

- conversations: `conv-41`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-48`, `conv-49`, `conv-50`
- question categories: `1`, `2`, `3`
- missing-gold rows excluded

Artifact:

- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\2026-04-22-typed-conversational-layer-direct.json`

## Measurement Method

This rerun used the substrate packet path directly:

- `build_summary_synthesis_memory_packets`
- primary `answer_candidate`
- `_expand_answer_from_context`
- repo-native `_matches_expected_answer`

So this is still a substrate-on-current-HEAD measurement, not a hand-judged spot check.

## Results

Overall:

- previous unseen slice: `31/580` (`5.34%`)
- current unseen slice: `64/580` (`11.03%`)
- net lift: `+33` correct
- relative gain: a little over `2x`

Per conversation:

- `conv-41`: `8/66` vs `7/66` (`+1`)
- `conv-42`: `6/88` vs `3/88` (`+3`)
- `conv-43`: `3/71` vs `3/71` (`flat`)
- `conv-44`: `2/61` vs `2/61` (`flat`)
- `conv-47`: `7/67` vs `7/67` (`flat`)
- `conv-48`: `18/73` vs `3/73` (`+15`)
- `conv-49`: `16/83` vs `1/83` (`+15`)
- `conv-50`: `4/71` vs `5/71` (`-1`)

## What Actually Improved

The gains line up with the new architectural layer, not with question-id patching.

What changed in the substrate:

- typed `loss_event` and `gift_event` atoms at ingest
- short-range same-speaker context carryover for pronoun-heavy turns like `she passed away`
- temporal normalization for conversational phrases such as:
  - `a few years ago`
  - `last year`
  - `two days ago`
- typed temporal evidence precedence so exact event evidence beats reflective summary contamination on temporal questions

That explains the shape of the lift:

- `conv-48` jumped because it contains exactly the kinship-heavy grief and gift memory pattern this layer was built to capture
- `conv-49` jumped because the wider evidence-routing cleanup preserved more exact supportable object and trip facts in the answer lane
- `conv-42` improved because older relative-time anchors stopped collapsing as aggressively to nearby salient dates

## What Did Not Improve Enough

The unseen lane is still weak overall at `64/580`.

The remaining failure shape is now clearer:

- `conv-43` and `conv-44` are still near floor
- `conv-50` slightly regressed
- the architecture is better on social-temporal conversational facts, but it is still not broadly strong enough on:
  - relation/social-graph joins
  - support/activity/place aggregation
  - typed list/count/object extraction beyond the currently recovered families
  - multi-hop conversational inference

## Current Read

This checkpoint is meaningful.

It is not benchmark noise and it is not just regression gardening:

- same unseen slice
- same scoring contract
- `31/580` to `64/580`

So the typed conversational layer is the right direction for real Telegram-style memory.

But it is still only the first layer.

## Next Build Order

1. Extend typed extraction beyond `loss_event` and `gift_event` into:
   - `support_event`
   - `shared_activity`
   - `relationship_edge`
   - `place_of_peace`
2. Add lightweight conversational indexes for:
   - entity/relation adjacency
   - time-sorted event lookup
3. Re-measure the same unseen `580`-question slice after each checkpoint.

## Bottom Line

The architecture got materially better on unseen LoCoMo social memory.

The right interpretation is:

- yes, the typed conversational memory layer works
- no, LoCoMo is not healthy yet
- yes, this is now moving through real architectural improvements instead of local patch accumulation
