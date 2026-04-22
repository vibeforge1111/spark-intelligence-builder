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
- `f3f9127`
  - `Add gated conversational shadow metrics`
- `4bea39a`
  - `Add typed temporal graph memory sidecar`
- `46be043`
  - `Add eval-only graph retrieval`
- `d684f4d`
  - `Add alias and commitment graph memory`
- `9f7f07e`
  - `Add typed graph hybrid shadow packets`
- `2ff415c`
  - `Add typed graph shadow answer eval`
- `6d4d76f`
  - `Add multi-family shadow answer eval`
- `9f4ba1e`
  - `Add CLI for multi-family shadow eval`
- `0c49964`
  - `Add negation and reported-speech graph memory`
- `6e1dd96`
  - `Add unknown conversational graph memory`

Measured lane:

- conversations: `conv-41`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-48`, `conv-49`, `conv-50`
- question categories: `1`, `2`, `3`
- missing-gold rows excluded

Artifact:

- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\2026-04-22-conversational-shadow-eval.json`
- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\2026-04-22-gated-conversational-shadow-eval.json`
- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\2026-04-22-exact-turn-shadow-selector.json`
- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\2026-04-22-exact-turn-shadow-answer-eval.json`
- `C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\cli-multi-shadow-smoke.json`

## What Was Measured

For each question, we compared three retrieval-coverage views:

- `summary`
  - current `summary_synthesis_memory` retrieved context only
- `conversational`
  - sidecar conversational retriever only
- `hybrid`
  - coverage from `summary OR conversational`
- `gated_hybrid`
  - coverage from `summary OR conversational`, but only when a question-family gate allows the conversational lane
- `exact_turn_hybrid`
  - coverage from `summary OR conversational`, but only when a broader exact-turn selector allows the conversational lane

This is retrieval coverage only, not final answer accuracy.

The question was:

- does the new conversational lane surface answer-bearing evidence that the current summary lane misses?

## Results

Overall on the unseen `580`-question slice:

- `summary`: `109/580` (`18.79%`)
- `conversational`: `60/580` (`10.34%`)
- `hybrid`: `130/580` (`22.41%`)
- `gated_hybrid`: `111/580` (`19.14%`)
- `exact_turn_hybrid`: `130/580` (`22.41%`)
- `hybrid_delta_vs_summary`: `+21`
- `gated_hybrid_delta_vs_summary`: `+2`
- `exact_turn_hybrid_delta_vs_summary`: `+21`

Interpretation:

- the conversational retriever is not strong enough to replace the current summary retrieval
- but it does add real retrieval coverage when combined with the current summary lane

Per conversation:

- `conv-41`: summary `7/66`, conversational `9/66`, hybrid `14/66`, gated `8/66`, exact-turn `14/66`
- `conv-42`: summary `13/88`, conversational `4/88`, hybrid `13/88`, gated `13/88`, exact-turn `13/88`
- `conv-43`: summary `14/71`, conversational `3/71`, hybrid `15/71`, gated `14/71`, exact-turn `15/71`
- `conv-44`: summary `16/61`, conversational `12/61`, hybrid `19/61`, gated `16/61`, exact-turn `19/61`
- `conv-47`: summary `10/67`, conversational `3/67`, hybrid `11/67`, gated `10/67`, exact-turn `11/67`
- `conv-48`: summary `24/73`, conversational `12/73`, hybrid `26/73`, gated `25/73`, exact-turn `26/73`
- `conv-49`: summary `12/83`, conversational `8/83`, hybrid `15/83`, gated `12/83`, exact-turn `15/83`
- `conv-50`: summary `13/71`, conversational `9/71`, hybrid `17/71`, gated `13/71`, exact-turn `17/71`

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

The gated experiment matters because it failed in an informative way:

- the first question-family gate only kept `+2` of the available `+21`
- `19` hybrid wins were left on the table
- the misses were not random noise; they clustered around exact personal facts that still live in raw conversational turns:
  - writing classes
  - who someone went with
  - what instruments someone plays
  - how many cars someone has owned
  - where someone was located
  - pet / dream / activity facts

The exact-turn selector matters because it recovered those same gains without opening the lane fully. On this slice it matched the unrestricted `hybrid` result exactly:

- it preserved the full `+21` over summary
- it still stayed narrower than “use conversational everywhere”
- it points to the right architectural distinction:
  - summary retrieval is strong for abstracted memory
  - conversational retrieval is strong for exact slot-filling from raw turns

Follow-up implementation status:

- an eval-only exact-turn hybrid packet builder now exists in the substrate
- it merges current summary packet context with conversational turns only when the exact-turn selector fires
- runtime is still unchanged
- this means the next pass can measure answer behavior on fused evidence instead of stopping at coverage-only analysis

## Heuristic Answer Eval

The first answer-side shadow check was run with the local deterministic `heuristic_v1` provider over the same unseen `580`-question slice.

Result:

- `summary`: `64/580` (`11.03%`)
- `exact_turn_hybrid`: `64/580` (`11.03%`)
- delta: `0`
- improved rows: `0`
- regressed rows: `0`

Interpretation:

- the retrieval gain is real
- the local heuristic answerer is not strong enough to convert the added exact-turn evidence into answer wins
- this means heuristic-only answer eval is not an adequate promotion signal for the new conversational lane

So the current honest read is:

- retrieval coverage improved
- answer accuracy did not move under the local heuristic evaluator
- real-LLM answer-side evaluation is required before any runtime promotion

## Cross-Check From Parallel Review

External architecture and evaluation review was consistent with the local measurements:

- root cause: summary compaction is dropping exact-span support needed for conversational memory
- additive layers are the right path, not replacing `summary_synthesis_memory`
- the best next substrate shape is a typed temporal / relationship graph with preserved provenance spans
- broad-synthesis regression must remain the primary overfit detector
- promotion should require real-LLM answer evaluation, not heuristic-only shadow passes

## Later Additive Progress

After the first exact-turn shadow pass, the substrate was extended with a typed graph sidecar instead of pushing runtime changes:

- alias bindings such as `Jo -> Joanna`
- commitment records such as `I'm going to a transgender conference this month`
- negation records such as `I've never been to Boston before`
- reported-speech records such as `The doctor said it's not too serious`
- unknown records such as `I can't remember such a game`

These are all provenance-preserving sidecar records. Runtime is still unchanged.

What this means architecturally:

- the system now has a better typed conversational substrate for social, temporal, and uncertainty-heavy chat memory
- we are no longer blocked on extraction/plumbing for the next eval step
- the remaining blocker is evaluation quality, not storage shape

Focused validation on the newest graph-memory layers is green in the domain repo:

- negation / reported-speech lane: `19 passed, 14 deselected`
- unknown-record lane: `10 passed, 27 deselected`

Those runs validate extraction, promotion, retrieval, and shadow-packet assembly, but they do not count as promotion evidence for runtime.

## Current Blocker

The real-provider comparison path is implemented, but the local machine does not currently expose a configured provider env for it.

Checked locally on `2026-04-22`:

- `OPENAI_API_KEY`: not set
- `DOMAIN_CHIP_MEMORY_OPENAI_MODEL`: not set
- `OPENAI_MODEL`: not set
- `MINIMAX_API_KEY`: not set
- `DOMAIN_CHIP_MEMORY_MINIMAX_MODEL`: not set
- `MINIMAX_MODEL`: not set

So the honest current state is:

- multi-family real-provider eval plumbing exists
- the local deterministic heuristic remains flat on answer quality
- promotion-grade answer comparison is blocked until a real provider is configured

When a provider is available, the intended command is:

```powershell
python -m domain_chip_memory.cli run-locomo-multi-shadow-eval C:\Users\USER\Desktop\domain-chip-memory\benchmark_data\official\LoCoMo\data\locomo10.json --provider openai:<model> --sample-id conv-41 --sample-id conv-42 --sample-id conv-43 --sample-id conv-44 --sample-id conv-47 --sample-id conv-48 --sample-id conv-49 --sample-id conv-50 --category 1 --category 2 --category 3 --exclude-missing-gold --write C:\Users\USER\.spark-intelligence\artifacts\locomo-unseen-slice\real-provider-multi-shadow.json
```

## Architectural Conclusion

The next correct architecture is not:

- replace `summary_synthesis_memory` retrieval with conversational retrieval

The next correct architecture is:

- keep current summary retrieval as the backbone
- add the conversational index as a second retrieval lane
- do not rely on a shallow regex-style family gate as the selector
- use the conversational lane where retrieval quality or exact-turn evidence is likely to beat summary abstraction

The first attempted gate was too narrow. It mostly captured kinship/support wording but missed many exact conversational facts that were still better served by the conversational index.

That means the next selector should be based less on coarse question family labels and more on answer mode / evidence mode:

- exact personal fact questions
- location / attendance / ownership / activity questions
- list/count questions tied to a specific person
- kinship/support questions

Do not promote the conversational lane broadly yet for:

- older temporal-anchor questions
- general unseen factoid traffic

## Recommended Next Step

Implement the next additive substrate layer before touching runtime:

1. Keep current summary retrieval unchanged.
2. Add a typed temporal / relationship graph sidecar with preserved source spans.
3. Promote exact-turn evidence as a first-class retrieval lane, not just a shadow metric.
4. Run real-LLM answer evaluation over:
   - summary-only packets
   - exact-turn hybrid packets
   - later graph/time fused packets
5. Compare on:
   - unseen `580`-question slice
   - focused `conv-48` / `conv-49` / `conv-50` packs
   - regression and soak guardrails
6. Only then consider runtime promotion.

The selector itself should keep scoring whether a question is better served by:
- abstract summary evidence
- exact conversational-turn evidence
7. Let the selector consider:
   - question shape
   - presence of person-specific slot filling
   - summary evidence quality
   - conversational evidence quality
 
On the current unseen slice, the exact-turn selector is the best shadow candidate so far.

That is the highest-probability path to improve real chat memory without repeating the last overfit mistake.
