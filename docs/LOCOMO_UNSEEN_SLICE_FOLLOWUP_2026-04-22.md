# LoCoMo Unseen Slice Followup 2026-04-22

## Scope

This follow-up measures the answer-present unseen LoCoMo slice after the substrate checkpoint:

- domain commit: `dc730b0`
- message: `Improve unseen LoCoMo evidence-first answer recovery`

Measured lane:

- conversations: `conv-41`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-48`, `conv-49`, `conv-50`
- question categories: `1`, `2`, `3`
- missing-gold rows excluded

## Results

Overall:

- `31/580` (`5.34%`)

Per conversation:

- `conv-41`: `7/66` (`10.61%`)
- `conv-42`: `3/88` (`3.41%`)
- `conv-43`: `3/71` (`4.23%`)
- `conv-44`: `2/61` (`3.28%`)
- `conv-47`: `7/67` (`10.45%`)
- `conv-48`: `3/73` (`4.11%`)
- `conv-49`: `1/83` (`1.20%`)
- `conv-50`: `5/71` (`7.04%`)

What clearly improved:

- `conv-47` moved from the previously observed `0/67` failure cluster to `7/67`.
- The fixed family was exactly the one we debugged: preference-noise suppression, better raw sentence extraction, broader answer context for LoCoMo evidence-first questions, and exact supportable answer synthesis.

What did not improve enough:

- The aggregate unseen lane is still very weak.
- `conv-49` remains the worst measured slice at `1/83`.
- `conv-42`, `conv-43`, `conv-44`, and `conv-48` remain near floor.

## New Failure Taxonomy

The postmortem is now sharper than the earlier "aggregate chatter" diagnosis.

### 1. We fixed one family, not the unseen distribution

The current checkpoint closes a specific failure class well:

- supportable fact questions where the right raw turn already exists
- the raw turn is being polluted by synthetic preference atoms or greeting/filler clauses
- the answer can be recovered by better evidence-first routing plus exact extraction

That explains the `conv-47` lift.

It does not solve the broader unseen set.

### 2. Many remaining misses are still retrieval misses, but of a different kind

Representative `conv-49` failures:

- `What kind of car does Evan drive?`
  - predicted: `hiking`
  - expected: `Prius`
- `Where has Evan been on roadtrips with his family?`
  - predicted: `Dealing with health issues has been tough, but it's made me appreciate the good moments more`
  - expected: `Rockies, Jasper`
- `Which country was Evan visiting in May 2023?`
  - predicted: `Take it easy, Evan`
  - expected: `Canada`
- `What new hobbies did Sam consider trying?`
  - predicted: `Picked up any new hobbies`
  - expected: `Painting, kayaking, hiking, cooking, running`

This is no longer just preference contamination. It is topic-routing failure into semantically adjacent chatter or question prompts.

### 3. We are still weak on typed list and count questions

Representative failures:

- `How many Prius has Evan owned?` -> expected `two`
- `How many roadtrips did Evan take in May 2023?` -> expected `two`
- `What new hobbies did Sam consider trying?` -> expected a multi-item list
- `What kinds of things did Evan have broken?` -> expected a two-item object list

The substrate still lacks dependable typed extraction for:

- object counts
- repeated owned-item counts
- multi-item hobby lists
- travel destination lists

### 4. We are still weak on older anchor-time normalization

Representative `conv-42` failures:

- `When did Joanna first watch "Eternal Sunshine of the Spotless Mind?"`
  - predicted: `A few years ago`
  - expected: `2019`
- `When did Nate win his first video game tournament?`
  - predicted: `January 2022`
  - expected: `the week before 21 Janury, 2022`
- `When did Joanna finish her first screenplay?`
  - predicted: `23 January 2022`
  - expected: `The Friday before 23 January, 2022`
- `When did Nate get his first two turtles?`
  - predicted: `18 March 2022`
  - expected: `2019`

So we still over-collapse relative and coarse temporal references into:

- month-level answers instead of event-relative windows
- nearby salient dates instead of the first/older anchor

### 5. We are still weak on relation and social-graph inference

Representative `conv-42` failures:

- `Is it likely that Nate has friends besides Joanna?`
  - expected: teammates on his video game team
- `What kind of interests do Joanna and Nate share?`
  - expected: watching movies, making desserts

This is the same structural gap the earlier postmortem identified:

- weak two-speaker relation expansion
- weak shared-interest synthesis
- weak inference from repeated co-activity evidence

## Updated Diagnosis

The current LoCoMo problem is now best described as three different substrate gaps:

1. Evidence contamination and clause selection
   - partially improved in `dc730b0`
2. Typed retrieval and extraction for lists, counts, owned objects, travel, and hobbies
   - still mostly open
3. Relation and temporal graph reasoning across unseen speaker pairs
   - still mostly open

So the remaining ceiling is not one bug. It is the absence of a general unseen LoCoMo retrieval schema.

## Highest-Leverage Next Improvements

### 1. Add typed list/count extractors before more phrase rescue

Target first:

- multi-item hobby lists
- owned-object counts
- trip-count aggregation
- destination list extraction
- object-type recall like car make/model

This should directly help `conv-49`.

### 2. Add older-anchor and event-relative temporal selection

Target failures where the current system chooses:

- a nearby salient date
- a coarse month/year
- or a generic `few years ago`

instead of:

- `first`
- `before <anchor date>`
- earlier historical anchor

This should directly help `conv-42`.

### 3. Add shared-interest and teammate/social-link inference

Need typed joins such as:

- person -> team -> teammates
- person -> repeated activity -> shared interest
- person -> owned pets/items -> count and duration

This is the missing bridge between exact evidence retrieval and the category-3 inference questions.

### 4. Keep LoCoMo category `1/2/3` evidence-first

Do not roll back the current evidence-first changes.

They did real work on `conv-47`, and the new regression should stay:

- real recovery on `Obesity`
- `bowling`
- `VR Club, McGee's, baseball game`
- `No`
- `John's favorite game is CS:GO, and James's favorite game is Apex Legends`
- `Likely yes`
- `Connecticut`

## Recommended Next Execution Order

1. Add typed list/count/object extraction for the `conv-49` family.
2. Add older-anchor and event-relative temporal routing for the `conv-42` family.
3. Add relation/social-graph inference for shared-interest and teammate questions.
4. Re-measure the same `31/580` unseen slice after each checkpoint instead of relying on raw full-lane LoCoMo totals.

## Bottom Line

`dc730b0` proved the earlier diagnosis was directionally correct:

- evidence-first routing matters
- preference/noise suppression matters
- exact raw-turn clause selection matters

But the unseen LoCoMo lane is still mostly blocked by:

- typed extraction gaps
- temporal anchor selection gaps
- weak relation graph inference

So the next honest LoCoMo work is not another broad architecture change.

It is a sequence of targeted substrate upgrades measured against the same unseen `31/580` slice.
