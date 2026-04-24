# Spark Intelligence v1 Onboarding CLI Spec

## 1. Purpose

This document defines the v1 onboarding CLI for `Spark Intelligence`.

The onboarding system should make it possible for a user to:

- install Spark Intelligence quickly
- configure a model provider or OpenAI-compatible endpoint
- connect one messaging surface
- connect the runtime to the Spark ecosystem
- start the gateway
- verify the setup with one doctor pass
- understand the basic Spark mental model without reading the whole architecture

This should feel closer to:

- `Hermes`: clean setup and gateway flow
- `OpenClaw`: guided onboarding, channel login, repair posture

But the final shape must stay Spark-native.

## 2. Design Goals

### 2.1 Fast First Success

The user should get to a first working message in minutes, not hours.

### 2.2 One Obvious Setup Path

There should be one default onboarding path for most users.

### 2.3 Foreground First

The system should work immediately in the foreground without requiring service installation.

### 2.4 Lightweight Install

The default install path should use:

- `npm`
- `npx`
- shell install scripts

It should not require:

- Docker
- Redis
- PM2
- custom daemon managers
- any heavy local platform

### 2.5 Explicit Security

The setup flow must make allowlists, pairing, and token handling obvious.

### 2.6 Spark-Native Orientation

The user should learn just enough of the Spark model to use the system well:

- one persistent agent
- Spark Researcher as the runtime core
- Spark Swarm for hard tasks
- domain chips and specialization paths as evolution surfaces

The user should not need a lecture before first use.

## 3. Core Product Thesis For Onboarding

The onboarding should communicate this in plain language:

`Spark Intelligence is one persistent agent you can message across channels, powered by Spark Researcher, deepened by Spark Swarm, and shaped over time by specialization paths and domain chips.`

This should appear during setup in a short and practical form.

## 4. Recommended Install Shapes

### 4.1 Primary Install Shapes

Recommended v1 options:

```bash
npx spark-intelligence@latest setup
```

or:

```bash
npm install -g spark-intelligence
spark-intelligence setup
```

Later optional convenience, if we ship a signed installer script:

```bash
curl -fsSLO <install-script>
less ./install.sh
bash ./install.sh
spark-intelligence setup
```

### 4.2 Default Promise

The installer should:

- install only the core runtime by default
- avoid adapter extras until they are enabled
- create config and state directories
- validate that the local environment is usable
- not declare success if critical prerequisites are missing

## 5. CLI Surface

The onboarding CLI should stay small in v1.

Recommended top-level commands:

- `spark-intelligence setup`
- `spark-intelligence doctor`
- `spark-intelligence gateway start`
- `spark-intelligence gateway status`
- `spark-intelligence channel add <channel>`
- `spark-intelligence channel remove <channel>`
- `spark-intelligence channel status`
- `spark-intelligence auth connect <provider>`
- `spark-intelligence auth status`
- `spark-intelligence agent inspect`
- `spark-intelligence install-autostart`
- `spark-intelligence uninstall-autostart`

Recommended job commands:

- `spark-intelligence jobs tick`
- `spark-intelligence jobs list`

Do not start with a giant command tree.

## 6. Primary Setup Flow

### 6.1 Recommended Default

The default command should be:

```bash
spark-intelligence setup
```

This should launch an interactive wizard.

### 6.2 Wizard Modes

Recommended setup modes:

- `QuickStart`
- `Advanced`
- `Import Existing Agent`

`QuickStart` should be the default and recommended path.

## 7. QuickStart Flow

The QuickStart path should do the minimum needed to become useful.

### 7.1 Step Order

1. Welcome and explain the product in two or three short lines
2. Choose install location and workspace
3. Choose model/provider
4. Connect provider credentials or endpoint
5. Choose first channel
6. Configure allowlist or pairing defaults
7. Select initial Spark specialization defaults
8. Run doctor
9. Start gateway
10. Show first message instructions

### 7.2 Example First-Run Script

The wizard should feel roughly like:

```text
Welcome to Spark Intelligence.

You are creating one persistent Spark-native agent.
It will run on Spark Researcher, can escalate through Spark Swarm, and can evolve through chips and specialization paths.

Let's get your first agent online.
```

## 8. Provider Setup

### 8.1 Supported Provider Shapes

v1 should support:

- OpenAI API
- Anthropic API
- OpenRouter
- custom OpenAI-compatible endpoint
- Spark-native provider defaults when available

### 8.2 Provider UX

The user should choose from:

- `OpenAI`
- `Anthropic`
- `OpenRouter`
- `Custom OpenAI-Compatible`

For each provider, the wizard should request only the required fields.

Examples:

- API key
- base URL for custom endpoint
- default model

### 8.3 Auth Connect Commands

Recommended:

```bash
spark-intelligence auth connect openai
spark-intelligence auth connect anthropic
spark-intelligence auth connect openrouter
spark-intelligence auth connect custom
```

### 8.4 Validation

The setup flow should validate:

- credentials are present
- the selected model or endpoint is reachable
- the response shape is compatible enough for runtime use

If validation fails, the wizard should not continue silently.

## 9. Channel Setup

### 9.1 v1 Channel Order

Recommended order:

1. Telegram
2. Discord
3. WhatsApp

### 9.2 Channel Add UX

Recommended command shape:

```bash
spark-intelligence channel add telegram
spark-intelligence channel add discord
spark-intelligence channel add whatsapp
```

The interactive setup can also call this internally.

### 9.3 Telegram Setup

The Telegram flow should ask for:

- bot token
- allowed user IDs or pairing policy
- optional home channel

The wizard should explain how to get:

- bot token from `@BotFather`
- user ID from a bot such as `@userinfobot`

### 9.4 Discord Setup

The Discord flow should ask for:

- bot token
- allowed user IDs
- optional home channel or default server/channel

The wizard should explain that:

- user IDs require Developer Mode
- DMs are the simplest first test

### 9.5 WhatsApp Setup

The WhatsApp flow should support a QR-based login path.

The wizard should ask for:

- mode
- allowed phone numbers or pairing policy
- optional home contact

The flow must make the operational reality obvious:

- WhatsApp setup is less trivial than Telegram
- QR linking is expected
- this is not the first recommended adapter unless the user specifically wants it

### 9.6 Channel Security Defaults

For all adapters, the safe defaults should be:

- deny by default
- allowlist or explicit pairing required
- no public group access by accident

## 10. Spark-Specific Setup

This is where Spark Intelligence must differ from OpenClaw and Hermes.

The wizard should ask for small but meaningful Spark-specific setup choices.

### 10.1 Initial Agent Shape

Recommended choices:

- `General Spark Researcher`
- `Builder`
- `Operator`
- `Research`

These should map to different default chip and path presets, not to completely different runtimes.

### 10.2 Initial Domain Chips

v1 should allow only a small set of safe starter chips.

The wizard should present them as optional specialization attachments, not mandatory complexity.

### 10.3 Initial Specialization Path

The user should choose:

- no specialization yet
- one starter path

Do not require the user to understand all of Spark's internal concepts at setup time.

### 10.4 Spark Explanation Layer

After setup succeeds, show a short orientation:

```text
Your agent runs on Spark Researcher.
Hard tasks can escalate through Spark Swarm.
Its specialization can evolve through chips and paths over time.

You do not need to manage that all now.
Start by talking to your agent normally.
```

## 11. Import Flow

### 11.1 Import Existing Agent Mode

Recommended command:

```bash
spark-intelligence setup --import
```

or:

```bash
spark-intelligence import openclaw
spark-intelligence import hermes
```

### 11.2 Import Scope

The onboarding wizard should be able to import:

- safe adapter config
- allowlists and pairing state
- session and identity mappings where compatible
- safe recurring intent

It should not import:

- foreign memory semantics
- opaque runtime state
- incompatible hidden caches

### 11.3 Import UX

The import flow should always be:

1. discover
2. show importable items
3. dry-run
4. confirm
5. validate
6. activate

## 12. Doctor And Validation

### 12.1 Doctor Position

`doctor` should be a first-class part of onboarding, not an afterthought.

Recommended command:

```bash
spark-intelligence doctor
```

### 12.2 What Doctor Should Check During Setup

- config validity
- provider validity
- channel credential presence
- adapter prereqs
- writable local state
- native autostart registration validity if enabled
- gateway startup readiness

### 12.3 Setup Failure Rule

If setup is incomplete or invalid:

- the wizard should say exactly what is missing
- the gateway should refuse to start unsafely
- doctor should provide the fastest repair path

## 13. Gateway Start

### 13.1 Primary Command

Recommended:

```bash
spark-intelligence gateway start
```

This should:

- start in the foreground by default
- print health and active adapter state
- print the active provider
- print the local control URL if one exists
- print the first message instructions

### 13.2 First Message Verification

After startup, the output should say something like:

```text
Gateway running.
Telegram adapter connected.
Allowed users: 1
Provider: OpenAI / gpt-5.4

Send your agent a message on Telegram to verify the connection.
```

## 14. Autostart

### 14.1 Principle

Autostart should be optional and native.

It should not require:

- Docker Compose
- PM2
- custom daemon supervisors

### 14.2 Command Shape

Recommended:

```bash
spark-intelligence install-autostart
spark-intelligence uninstall-autostart
```

### 14.3 Native Targets

Recommended native wrappers:

- macOS: LaunchAgent
- Linux: `systemd --user`
- Windows: Task Scheduler

The native wrappers should invoke Spark Intelligence commands like:

- `spark-intelligence gateway start`
- `spark-intelligence jobs tick`

## 15. Config And State Layout

### 15.1 Recommended Layout

Recommended default home:

```text
~/.spark-intelligence/
```

Suggested structure:

```text
~/.spark-intelligence/
|- config.yaml
|- .env
|- state.db
|- logs/
|- adapters/
`- artifacts/
```

### 15.2 Config Principles

- one canonical config surface
- secrets separated where practical
- strict validation
- explicit channel sections
- explicit provider section
- explicit Spark linkage section

## 16. First-Run Education

The onboarding should teach without overloading.

Recommended information budget:

- what the agent is
- how to message it
- what pairing or allowlists mean
- what Spark adds
- how to run doctor

Do not dump:

- full Spark architecture
- domain chip theory
- swarm internals
- memory internals

## 17. Non-Goals For v1 Onboarding

Do not try to make onboarding also be:

- a full dashboard product
- a visual workflow builder
- a memory configuration lab
- a multi-channel enterprise deployment suite
- a plugin marketplace

## 18. Recommended v1 Happy Path

This is the path we should optimize for first.

```bash
npx spark-intelligence@latest setup
spark-intelligence doctor
spark-intelligence gateway start
```

Inside setup:

- choose OpenAI or OpenRouter
- connect Telegram
- add one allowed user
- choose a starter Spark profile
- finish

Then:

- send one Telegram message
- confirm reply
- only after that consider autostart or more channels

## 19. Final Decision

Spark Intelligence v1 should have a CLI-first onboarding flow that is:

- installable in minutes
- secure by default
- foreground-first
- simple enough for `npx`
- explicit enough to debug
- Spark-native without becoming concept-heavy

The user should not need to understand the whole ecosystem to get value.

They should get one working Spark agent first.
