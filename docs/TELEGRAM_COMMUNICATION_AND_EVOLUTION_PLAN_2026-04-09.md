# Telegram Communication And Evolution Plan 2026-04-09

Historical note:

- this plan was written against the Builder-owned Telegram runtime shape
- the current stable production path uses `spark-telegram-bot` as the Telegram ingress owner
- the communication and reply-shaping guidance here is still useful, but ingress ownership statements should be read in that older context

## 1. Purpose

This note defines one focused workstream for the Telegram operator system:

- make the bot feel more human, composed, and useful
- make Spark Swarm / specialization-path / autoloop outputs easier to understand
- make agent improvement visible to the operator
- verify that improvement signals actually feed back into the agent system rather than remaining isolated artifacts

This is not only a formatting task.

It is a product-quality and systems-quality task.

The goal is for the Telegram bot to become a real operating console for:

- observing agents
- running bounded actions
- understanding what changed
- seeing whether the agent is actually improving
- feeling that improvement in how the system communicates and behaves

## 2. Current Truth

What is already working:

- Builder-owned logic handles the Telegram-facing reply and routing behavior, but the current stable production ingress owner is `spark-telegram-bot`
- Builder routes natural-language Swarm reads and actions
- Builder routes local Swarm bridge actions
- Builder surfaces autoloop pause / continue / session inspection
- Builder can use browser evidence through `spark-browser-extension`

What is still weak:

- replies often feel like system output blocks rather than conversation
- the bot does not always lead with the answer first
- the bot does not yet consistently explain why a change matters
- the bot does not yet give a strong sense that loop outputs are improving the agent
- the current `startup-operator` autoloop is honestly blocked from real score improvement until the benchmark can consume repo-owned mutations

## 3. Communication North Star

The target style should follow the strengths of high-quality assistant communication, including Claude-like strengths, without copying quirks mechanically.

### What To Borrow

- answer first
- calm, capable tone
- layered detail instead of one big dump
- explicit uncertainty when needed
- natural transitions
- strong next-step suggestions
- human wording even for technical state

### What Not To Borrow Blindly

- excessive softness
- unnecessary hedging
- decorative verbosity
- vague empathy that hides operational truth
- freeform style that weakens deterministic runtime replies

### Desired Reply Feel

The bot should feel like:

- a strong operator assistant
- technically grounded
- concise by default
- easy to scan
- confident but honest

It should not feel like:

- pasted bridge output
- a CI log
- a database dump
- a generic LLM chat answer that ignores the system state

## 4. Workstream A: Reply Composition Layer

### Goal

Add a composition layer after intent resolution and before Telegram delivery.

### Design Rule

Intent routing stays deterministic.

Reply shaping happens after the system already knows:

- what intent fired
- what structured result came back
- what state changed
- what next step is actually valid

### Required Reply Shapes

Implement distinct output templates for:

- status check
- hosted read summary
- hosted action confirmation
- autoloop paused
- autoloop finished
- autoloop session inspection
- lane insight summary
- browser-backed site opinion
- browser-backed search answer
- blocked / unavailable / auth-failed state

### Default Reply Structure

For most operator replies:

1. verdict
2. key reason or outcome
3. compressed supporting details
4. next useful action

Example shape:

- `Startup Operator autoloop is paused.`
- `It hit the no-gain guard before another round, so nothing changed yet.`
- `Current score is 0.6222, best score is still 0.6222, and the last candidate was reverted.`
- `Next: continue with force, inspect the latest session, or review the latest insight.`

### Specific Composition Improvements

- lead with the answer, not the metadata
- collapse repetitive labels where possible
- separate long outputs into readable chunks
- avoid dumping raw paths unless they help
- keep lists short and purposeful
- turn dense evidence into compressed summaries
- use the final line for the next action when there is one

## 5. Workstream B: Communication Benchmark And Evaluation Harness

### Goal

Create a repeatable benchmark for communication quality.

### Benchmark Prompt Set

Use real operator prompts already common in the live bot:

- `show me swarm paths`
- `show me the latest Startup Operator autoloop session`
- `continue the Startup Operator autoloop in swarm for 1 more round anyway`
- `show me Startup Operator insights in swarm`
- `what changed in swarm for Startup Operator`
- `browse vibeship.co and tell me what you think`
- `Search the web for Example Domain and cite the source you used.`
- blocked cases:
  - autoloop benchmark-coupling block
  - auth refresh failure
  - browser unavailable

### Rubric

Score each reply on:

- first-line usefulness
- readability
- tone
- grounding
- next-step usefulness
- brevity discipline
- operator confidence

### Reference Style Study

Use a strong assistant reference style as a benchmark.

Recommended method:

1. take the benchmark prompt set
2. write or collect high-quality reference answers in a Claude-like style
3. extract style rules from them
4. do not train to mimic wording directly
5. instead implement deterministic reply-shaping rules that reproduce the good qualities

### Test Harness

Add:

- unit tests for reply assembly functions
- golden response tests for representative runtime results
- Telegram simulation tests that verify both routing and shaped output
- before / after comparison snapshots for high-value prompts

## 6. Workstream C: Natural-Language Expansion

### Goal

Make the operator surface feel natural without becoming vague or unsafe.

### Areas To Expand

- “what changed?” summaries
- “what should I do next?” guidance
- lane-specific reads
- loop / session / rerun follow-ups
- hosted-action requests that reference “latest” or lane labels
- agent-health and system-health questions

### Rule

Natural-language support should stay bounded:

- explicit enough to map to one clear intent
- rejected cleanly when ambiguous
- never allowed to silently trigger risky mutation work

## 7. Workstream D: Evolution Feedback Loop Back Into The Agent

### Goal

Make the operator feel and verify that Spark Swarm / specialization-path work is actually improving the agent.

### Desired Product Truth

When a loop produces:

- an insight
- a mastery
- an upgrade
- a benchmark win
- a better policy

the operator should be able to see:

- what improved
- where it came from
- whether it was adopted
- whether the agent now behaves differently because of it

### Required Feedback Path

The system needs a clear chain:

1. run / autoloop / benchmark produces evidence
2. evidence becomes insight / mastery / upgrade / delivery state
3. Builder can observe that structured state
4. Builder can explain it in Telegram
5. Builder can confirm whether it changed the effective agent behavior

### What To Verify

- does a new insight change what the agent later says or recommends?
- does a reviewed mastery actually affect active behavior?
- does an upgrade change lane outputs or strategy?
- can the bot explain the difference between:
  - proposed
  - reviewed
  - delivered
  - actually effective

### Product Requirement

The operator should not have to infer improvement from raw artifact files.

The bot should be able to say things like:

- `Startup Operator learned a stronger benchmark-backed lesson about risk management.`
- `That lesson has been absorbed, but it has not yet changed the benchmark-winning policy.`
- `This upgrade is delivered and now affects the active lane behavior.`

## 8. Workstream E: Gaps To Close

These are the current known gaps that should be carried into tomorrow.

### A. Startup Bench Causality Gap

- current `startup-operator` mutations do not affect the executed Startup Bench baseline
- autoloop score improvement is therefore blocked by architecture

### B. Agent-Behavior Feedback Visibility Gap

- the system can show insights and sessions
- it does not yet clearly prove that those outputs changed agent behavior

### C. Communication Quality Gap

- replies are correct but still too blocky
- composition quality varies by intent
- there is no dedicated reply benchmark harness yet

### D. Improvement Narrative Gap

- the bot can say what happened
- it does not yet consistently say what improved and why the operator should care

### E. Test Coverage Gap

- we have routing and behavior tests
- we need clearer reply-shape tests and feedback-loop verification tests

## 9. Concrete Tasks For Tomorrow

### Track 1. Communication Layer

- define reply templates by intent type
- implement a reply-composition helper layer
- add golden output tests for high-value runtime replies
- tighten autoloop and browser-answer formatting
- add better “what changed?” and “next step” summaries

### Track 2. Reference Style Study

- build a benchmark prompt set
- draft strong reference answers in a Claude-like quality bar
- extract reusable principles
- codify those into reply rules

### Track 3. Evolution Feedback

- trace how Swarm insights/masteries/upgrades currently surface into Builder
- identify where behavior adoption is visible vs missing
- add explicit summaries for:
  - proposed vs adopted
  - delivered vs effective
  - observed improvement vs assumed improvement

### Track 4. Real Improvement Causality

- inspect Startup Bench for a real mutable operator input seam
- decide whether to extend Startup Bench or replace this path adapter
- keep the current honest block in place until causality is real

## 10. Test Plan

### Unit Tests

Add tests for:

- reply composition helpers
- first-line verdict generation
- next-step generation
- blocked-state messaging
- autoloop summary compression
- browser-backed source summary formatting

### Integration Tests

Add or extend simulated Telegram tests for:

- status reads
- lane summaries
- autoloop paused
- autoloop continued
- browser-backed opinion
- benchmark-coupling blocked state

### Golden Transcript Tests

Store representative expected outputs for:

- best current style
- blocked cases
- browser evidence cases
- loop/session cases

These should fail when reply quality regresses.

### Live Operator Checks

Use the real Telegram bot to verify:

- the answer comes first
- details are readable
- next step is helpful
- the bot sounds human but still truthful
- loop outputs feel connected to improvement, not just to recordkeeping

## 11. Success Criteria

This workstream is successful when:

- Telegram replies feel composed, human, and useful
- operators can understand system state without reading raw artifacts
- improvement signals are visible and explained
- adopted improvement can be distinguished from proposed improvement
- autoloop and Swarm outputs feel like part of one evolving agent system
- communication quality is covered by tests instead of taste alone

## 12. Bottom Line

Tomorrow should not only improve what the system can do.

It should improve how clearly the system explains itself, how visibly it demonstrates improvement, and how convincingly it acts like one evolving operator-grade agent.
