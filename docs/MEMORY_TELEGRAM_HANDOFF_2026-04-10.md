# Memory Telegram Handoff 2026-04-10

## Scope

This handoff covers Spark Builder memory capture and `domain-chip-memory` replay alignment for Telegram profile facts, with focus on startup/founder phrasing and country-versus-city capture.

## Commits Landed

### `spark-intelligence-builder`

- `30e5d2d` `Support more natural Telegram profile fact phrasing`
- `293a51b` `Prefer recent founder facts for startup queries`
- `c6e6b2a` `Support more natural startup and founder phrasings`
- `8d16147` `Recognize in-country Telegram memory phrasing`
- `9b73db9` `Recognize moved-to country memory phrasing`
- `b8fd8b1` `Recognize live-in country memory phrasing`
- `623d842` `Normalize explicit country aliases`
- `a0edbb5` `Improve identity summary memory retrieval`
- `e7839f2` `Prefer latest values in identity summaries`
- `c6b3ce5` `Soften mixed geography identity summaries`

### `domain-chip-memory`

- `1859de9` `Align desktop startup replay with founder facts`
- `1ab814a` `Cover mixed city and country state replay`

## What Now Works

### Founder and startup phrasing

Builder now captures and answers:

- `I founded Atlas Labs.`
- `I started Atlas Labs.`
- `I built Atlas Labs.`
- `I launched Atlas Labs.`
- `I founded a startup called Atlas Labs.`
- `Atlas Labs is my startup.`
- `I run Atlas Labs.`

Startup queries now prefer newer `profile.founder_of` over stale `profile.startup_name`, so `What is my startup?` can correctly answer `You created Atlas Labs.` even when an older startup value still exists.

### Country versus city capture

Builder now correctly separates:

- `I'm in Canada now.` -> `profile.home_country=Canada`
- `I'm in Abu Dhabi now.` -> `profile.city=Abu Dhabi`
- `I moved to Canada.` -> `profile.home_country=Canada`
- `I moved to Dubai.` -> `profile.city=Dubai`
- `I live in Canada.` -> `profile.home_country=Canada`
- `I live in UAE.` -> `profile.home_country=UAE`
- `I live in the US.` -> `profile.home_country=United States`
- `I live in Dubai.` -> `profile.city=Dubai`

The leading-`the` normalization now also helps explicit country forms like `the US` and `the UK`.

### Identity-summary retrieval

Builder identity-summary retrieval now:

- recognizes `Who am I?`
- recognizes `What do you know about me?`
- recognizes `What do you remember about me?`
- keeps startup and founder semantics separate in the summary answer
- prefers the latest saved value for each predicate instead of the oldest one
- includes country as a separate sentence even when city is also present
- uses a neutral country sentence when both city and country are present, to avoid claiming an inconsistent combined base

That means a saved `profile.startup_name=Seedify` plus `profile.founder_of=Spark Swarm` now renders as:

- `You founded Spark Swarm.`
- `Your startup is Seedify.`

instead of the incorrect merged phrasing `You founded Seedify and Spark Swarm.`

### Desktop replay alignment

`domain-chip-memory` builder-state replay now:

- preserves founder-versus-startup recency correctly for startup queries
- replays mixed `profile.home_country` and `profile.city` states correctly
- keeps KB compilation valid on the latest probe homes used in this pass

## Live Telegram Validation Performed

Fresh probe homes were cloned before each live pass and tested through:

- `python -m spark_intelligence.cli gateway ask-telegram "<message>" --home <probe-home>`

Validated live:

- `I'm in Canada now.` -> `I'll remember your country is Canada.`
- `What country do I live in?` -> `Your country is Canada.`
- `I'm in Abu Dhabi now.` -> `I'll remember you live in Abu Dhabi.`
- `Where do I live?` -> `You live in Abu Dhabi.`
- `I moved to Canada.` -> `I'll remember your country is Canada.`
- `I moved to Dubai.` -> `I'll remember you live in Dubai.`
- `I live in Canada.` -> `I'll remember your country is Canada.`
- `I live in the US.` -> `I'll remember your country is United States.`
- `I'm from the US.` -> `I'll remember your country is United States.`
- `Who am I?` -> identity summary from memory
- `What do you remember about me?` -> identity summary from memory
- mixed-state identity summary now reflects latest saved founder and country values instead of stale ones
- mixed city-plus-country summaries now render country as `Your country is ...` instead of `You're based in ...`

## Tests Added

### Builder

In [tests/test_memory_orchestrator.py](C:/Users/USER/Desktop/spark-intelligence-builder/tests/test_memory_orchestrator.py):

- `I'm in Canada now.` country regression
- `I'm in Abu Dhabi now.` city regression
- `I moved to Canada.` country regression
- `I moved to Dubai.` city regression
- `I live in Canada.` country regression
- `I live in UAE.` alias regression
- `I live in the US.` alias regression
- `I live in Dubai.` city regression
- explicit `the US` / `the UK` / `the UAE` normalization regressions
- identity-summary retrieval wording regression for mixed startup plus founder facts
- `What do you remember about me?` identity-summary query regression
- identity-summary last-write-wins regression for repeated profile predicates

### Domain replay

In [tests/test_cli.py](C:/Users/USER/Desktop/domain-chip-memory/tests/test_cli.py):

- mixed current-state replay covering:
  - country write
  - country query
  - city write
  - city query

## Useful Probe Homes and Outputs

Builder probe homes created during this pass:

- `.tmp-home-live-telegram-in-country-supported-20260410173000`
- `.tmp-home-live-telegram-moved-country-supported-20260410174500`
- `.tmp-home-live-telegram-live-country-supported-20260410180000`

Domain replay outputs created during this pass:

- `C:\Users\USER\Desktop\domain-chip-memory\tmp\in_country_supported_intake.json`
- `C:\Users\USER\Desktop\domain-chip-memory\tmp\moved_country_supported_intake.json`

## Best Next Continuation

1. Decide whether travel-style utterances such as `I'm in Paris right now` should always stay city-only or whether there should be stronger residence-versus-location language separation.
2. Decide whether the system should try to reconcile city-country pairs semantically, or whether neutral saved-fact wording is the right long-term policy.
3. Continue from Telegram memory retrieval into KB-side summarization quality and answer selection, now that the main direct memory-query paths are materially stronger.
