# Spark Installer Standard v1

Date: 2026-04-22
Status: proposed source of truth

## 1. Purpose

This document defines the install and onboarding standard for the Spark system.

It exists to solve the exact failure mode seen on 2026-04-22:

- three repos
- three setup stories
- overlapping Telegram surfaces
- overlapping `.env` questions
- no obvious ownership model

The installer must make Spark feel like one system, not like a bundle of partially
connected repos.

## 2. Product Promise

The supported first-run shape is:

```bash
brew install spark
spark setup
spark start
```

The user should not need to:

- download zip files
- decide between `pip`, `npm`, `bun`, or `uv`
- read multiple READMEs to understand ownership boundaries
- guess which module gets the Telegram bot token
- edit multiple `.env` files by hand

## 3. Core Install Principles

### 3.1 One Front Door

All module discovery, install, config, start, stop, status, and uninstall actions
must flow through the `spark` CLI.

Supported:

- `spark setup`
- `spark install <module>`
- `spark start`
- `spark status`
- `spark doctor`

Unsupported as the primary onboarding path:

- manual repo zip downloads
- manual `.env` editing across multiple repos
- repo-specific install commands as the default user path

### 3.2 One Telegram Ingress Owner

One Telegram bot token must have one ingress owner.

That owner:

- receives Telegram updates
- validates webhook or polling ownership rules
- sends Telegram replies

All other modules sit behind that owner.

This rule is not optional.

### 3.3 One Config Wizard

The user should answer each question once.

The installer then writes the right derived config to the right modules.

The user should not be asked for the same secret or path in multiple places.

### 3.4 One Status Surface

The user must be able to run:

```bash
spark status
```

and immediately see:

- what is installed
- what is running
- what is unhealthy
- who owns Telegram ingress
- what fix to apply next

## 4. Canonical Module Roles

The current Spark starter stack has three distinct roles.

### 4.1 `spark-intelligence-builder`

Role:

- Spark runtime core
- identity, memory, policy, routing, operator control

Does:

- reason
- persist runtime state
- connect to providers
- own Spark-specific behavior
- expose the Builder CLI and per-request bridge entrypoints used behind the gateway

Does not, in the current supported split profile:

- own the production Telegram bot token
- receive live Telegram updates directly
- act as the default Telegram ingress daemon

### 4.2 `spark-telegram-bot`

Role:

- Telegram ingress gateway

Does:

- own the Telegram bot token
- own webhook or polling ingress
- dedupe updates
- enforce single-owner startup
- relay mission control requests
- send Telegram replies

Does not:

- become the main Spark runtime
- own mission state
- become the primary config truth for the whole system

### 4.3 `spawner-ui`

Role:

- execution plane and visual control surface

Does:

- run mission-style work
- expose mission-control APIs
- provide the local dashboard and canvas

Does not:

- own the Telegram bot token
- receive Telegram webhooks directly

## 5. Canonical Telegram Ownership Model

This must be the installer's explicit user-facing model.

### 5.1 Current Supported Profile

Current supported split profile:

```text
Telegram
  -> spark-telegram-bot
  -> spark-intelligence-builder
  -> spawner-ui when execution is needed
```

### 5.2 Secret Ownership

In the current supported split profile:

- `TELEGRAM_BOT_TOKEN` belongs to `spark-telegram-bot` only
- Telegram webhook secret belongs to `spark-telegram-bot` only
- Builder must not ask for the Telegram bot token in the default split install
- Spawner UI must never ask for the Telegram bot token

### 5.3 Future Migration Rule

Builder may later absorb Telegram ingress.

If that happens:

- Builder becomes the new single ingress owner
- `spark-telegram-bot` is retired or disabled
- there must still be exactly one ingress owner

The installer must never allow both Builder and gateway to own the same token at
the same time.

## 6. What The User Should See During Setup

The wizard should present bundles, not repos.

Recommended starter bundle:

- `Telegram Agent Starter`

Resolved modules:

- `spark-telegram-bot`
- `spark-intelligence-builder`
- `spawner-ui`

What the wizard should say in plain English:

```text
Telegram messages enter through Spark Telegram Gateway.
Spark Intelligence Builder is the agent runtime and memory layer.
Spawner UI is the execution and mission-control surface.

Your Telegram bot token will be stored only for Spark Telegram Gateway.
Builder and Spawner will connect behind it automatically.
```

That sentence is mandatory.

## 7. Setup Flow For The Telegram Starter Bundle

Recommended order:

1. Detect local tools:
   - Claude Code auth
   - `uv`
   - `bun`
   - supported OS package manager
2. Show bundle:
   - Telegram Gateway
   - Spark Runtime
   - Spawner UI
3. Show the ownership map:
   - gateway owns Telegram token
   - Builder owns runtime and memory
   - Spawner owns mission execution
4. Collect deduped inputs:
   - Telegram bot token
   - allowed Telegram user IDs or pairing policy
   - provider selection
   - only the provider secrets actually required
5. Install modules and write derived config
6. Run healthchecks
7. Start all startable services
8. Print first-use instructions

## 8. What The Wizard Must Ask

### 8.1 Telegram Questions

Only once:

- Telegram bot token
- allowed user or pairing mode

Only if webhook mode is selected:

- public webhook URL
- webhook secret

### 8.2 Provider Questions

Only once, deduped across modules:

- primary provider
- fallback provider if requested
- required secrets for those providers

If Claude Code is detected and authenticated locally:

- do not ask for `ANTHROPIC_API_KEY` by default
- present local Claude auth as a detected capability

### 8.3 Questions The Wizard Must Not Ask In The Starter Flow

- manual repo paths unless autodetection fails
- whether to use `pip` or `npm`
- whether to create a venv
- duplicate provider keys across multiple repos
- Telegram token for multiple modules

## 9. `spark.toml` Contract Extensions Required For v1

The existing manifest direction is correct, but not complete enough for a
multi-module Spark install.

### 9.1 Module Identity

Every module must declare:

```toml
[module]
name = "spark-telegram-bot"
version = "1.0.0"
kind = "service" # service | app | chip-pack | skill-pack | library
plane = "ingress" # ingress | runtime | execution | ui | content
```

### 9.2 Capability Contract

Every module must declare:

```toml
[provides]
capabilities = ["telegram.ingress", "telegram.reply"]
```

and:

```toml
[needs]
capabilities = ["spark.runtime", "mission.execution"]
```

### 9.3 Resource Claims

Capabilities alone are not enough.

Every module must also declare concrete claims:

```toml
[claims]
secrets = ["telegram.bot_token"]
ports = [8788]
routes = ["http.spawner-events"]
```

This is how the installer prevents collisions such as:

- two modules trying to own the same bot token
- two modules trying to bind the same port without agreement
- two modules trying to own the same public ingress route

### 9.4 Run Contract

Every service or app module must declare:

```toml
[run.default]
command = "bun run dev"
cwd = "."
ready_check = "http://127.0.0.1:4174/healthz"
```

Without this, `spark start`, `spark stop`, and `spark logs` cannot be consistent.

Runtime or bridge modules that are invoked on demand rather than held open as a
foreground service may omit `[run.default]` and declare only:

- healthcheck
- entrypoints
- installer profile role

### 9.5 Config Generation Contract

Every module must declare how installer-owned config becomes module-owned files:

```toml
[config]
format = "env"
output = ".env"
```

The installer writes these files from canonical state under `~/.spark/`.
Users should not hand-edit them in the supported path.

## 10. Required Capability Model For The Starter Stack

### 10.1 `spark-telegram-bot`

Should provide:

- `telegram.ingress`
- `telegram.reply`
- `telegram.mission-control`

Should need:

- `spark.runtime`
- `mission.execution`

Should claim:

- `telegram.bot_token`
- `telegram.webhook_secret`
- `http.spawner-events`

### 10.2 `spark-intelligence-builder`

Should provide:

- `spark.runtime`
- `spark.memory`
- `spark.identity`
- `spark.routing`

Should need:

- provider capability

Should not claim in the default split profile:

- `telegram.bot_token`

### 10.3 `spawner-ui`

Should provide:

- `mission.execution`
- `mission.dashboard`

Should need:

- provider capability for the configured execution mode

Should not claim:

- `telegram.bot_token`

## 11. Status Contract

`spark status` must show:

- module name
- version
- healthy or unhealthy
- ingress owner
- important bound endpoint
- repair hint when red

Example:

```text
Spark v0.1.0

✓ spark-telegram-bot       ingress owner for @SparkAGI_bot, webhook healthy
✓ spark-intelligence       runtime ready, provider connected, memory healthy
✓ spawner-ui               http://localhost:4174, mission control ready
```

If Telegram is misconfigured:

```text
✗ spark-telegram-bot       missing bot token - run `spark setup --repair telegram`
```

## 12. Registry And CI Standards

A module must not enter the blessed registry unless it passes all of these:

1. Ships a valid `spark.toml`
2. Ships a real README whose first heading matches the module name
3. Ships a lockfile matching the declared runtime tool
4. Contains no hard-coded user-specific absolute paths in tracked install-facing files
5. Contains no deprecated platform names in install-facing config examples
6. Has a working healthcheck command
7. Installs cleanly on clean macOS and Linux runners

## 13. Hard Rules For Docs And Config

### 13.1 Path Rule

Tracked docs and config examples must use:

- `$SPARK_HOME`
- `$SPARK_MODULE_HOME`
- `$SPARK_STATE_DIR`
- `$SPARK_LOG_DIR`

Never:

- `C:/Users/USER/Desktop/...`

### 13.2 Secret Rule

Install-facing docs must explain secret ownership by module.

For Telegram starter:

- only gateway gets bot token
- Builder does not get bot token
- Spawner does not get bot token

### 13.3 README Rule

A module README must answer:

1. What role does this module play?
2. What does it own?
3. What does it not own?
4. What does the installer do with it?

## 14. Immediate Retrofit Plan

Apply this standard to the first three modules:

1. `spark-telegram-bot`
   - mark as current Telegram ingress owner
   - remove stale Mind V5 install guidance from install-facing surfaces
   - declare token ownership clearly
2. `spark-intelligence-builder`
   - mark as runtime core, not Telegram ingress owner in the current supported profile
   - stop presenting hard-coded local paths as normal setup
3. `spawner-ui`
   - replace incorrect README
   - mark as execution plane only
   - stop looking like a separate install story

## 15. Final Rule

If a first-time user cannot answer these questions after one installer run, the
install design is still wrong:

1. What should I install?
2. Which module gets the Telegram bot token?
3. Which module is the actual Spark runtime?
4. Which module runs missions and the dashboard?
5. How do I check if the stack is healthy?

For the current starter stack, the answers must be:

1. Install the Telegram Starter bundle.
2. The Telegram bot token goes only to `spark-telegram-bot`.
3. `spark-intelligence-builder` is the Spark runtime.
4. `spawner-ui` runs missions and the dashboard.
5. Use `spark status` and `spark doctor`.
