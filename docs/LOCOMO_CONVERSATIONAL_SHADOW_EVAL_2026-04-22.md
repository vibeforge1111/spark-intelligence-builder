# LoCoMo Conversational Shadow Eval 2026-04-22

## Scope

This follow-up evaluates the new sidecar conversational memory work in shadow only.

Runtime was not changed.

Domain checkpoints used:

- `fa81f35`
  - `Add sidecar conversational memory index`
- `76cfcec`
  - `Add eval-only conversational retriever`
- `8dc5f67`
  - `Add conversational shadow coverage eval`

Measured lane:

- conversations: `conv-41`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-48`, `conv-49`, `conv-50`
- question categories: `1`, `2`, `3`
- missing-gold rows excluded

Artifact:

- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\2026-04-22-conversational-shadow-eval.json`

## What Was Measured

For each question, we compared three retrieval-coverage views:

- `summary`
  - current `summary_synthesis_memory` retrieved context only
- `conversational`
  - sidecar conversational retriever only
- `hybrid`
  - coverage from `summary OR conversational`

This is retrieval coverage only, not final answer accuracy.

The question was:

- does the new conversational lane surface answer-bearing evidence that the current summary lane misses?

## Results

Overall on the unseen `580`-question slice:

- `summary`: `109/580` (`18.79%`)
- `conversational`: `60/580` (`10.34%`)
- `hybrid`: `130/580` (`22.41%`)
- `hybrid_delta_vs_summary`: `+21`

Interpretation:

- the conversational retriever is not strong enough to replace the current summary retrieval
- but it does add real retrieval coverage when combined with the current summary lane

Per conversation:

- `conv-41`: summary `7/66`, conversational `9/66`, hybrid `14/66`
- `conv-42`: summary `13/88`, conversational `4/88`, hybrid `13/88`
- `conv-43`: summary `14/71`, conversational `3/71`, hybrid `15/71`
- `conv-44`: summary `16/61`, conversational `12/61`, hybrid `19/61`
- `conv-47`: summary `10/67`, conversational `3/67`, hybrid `11/67`
- `conv-48`: summary `24/73`, conversational `12/73`, hybrid `26/73`
- `conv-49`: summary `12/83`, conversational `8/83`, hybrid `15/83`
- `conv-50`: summary `13/71`, conversational `9/71`, hybrid `17/71`

## Main Read

The sidecar conversational retriever is directionally useful, but only as an additive lane right now.

It loses badly as a standalone replacement because it is still too weak on:

- broader temporal-anchor questions
- non-social unseen queries
- mixed conversational scenes where current summary retrieval already has decent recall

But it does recover missing evidence families that the current summary lane often drops:

- family-hobby turns
- grief-support turns
- social-memory exact-support turns

That is why `hybrid` beats `summary` by `+21` even though `conversational` alone is worse.

## Architectural Conclusion

The next correct architecture is not:

- replace `summary_synthesis_memory` retrieval with conversational retrieval

The next correct architecture is:

- keep current summary retrieval as the backbone
- add the conversational index as a second retrieval lane
- fuse the two lanes for specific question families first

Best first fusion targets:

- kinship-heavy social memory
- grief/support questions
- family-hobby and family-memory questions
- relationship / shared-activity questions

Do not promote the conversational lane broadly yet for:

- older temporal-anchor questions
- general unseen factoid traffic

## Recommended Next Step

Implement a gated hybrid retrieval experiment in shadow:

1. Keep current summary retrieval unchanged.
2. Add conversational retrieval only for:
   - kinship/social-memory questions
   - support/peace/grief questions
   - family-hobby / family-memory questions
3. Union the evidence sets before answer synthesis.
4. Re-measure:
   - unseen `580`-question slice
   - focused `conv-48` / `conv-49` / `conv-50` packs
   - regression and soak guardrails

That is the highest-probability path to improve real chat memory without repeating the last overfit mistake.
