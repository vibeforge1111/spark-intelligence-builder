# Spark Voice Runtime Self-Awareness

This is the current source-of-truth map for voice inside Spark. It reflects the stable Telegram path tested on May 9, 2026.

License: AGPL-3.0-or-later.

## Stable Runtime Path

1. Telegram receives a text, voice, or audio message.
2. `spark-telegram-bot` normalizes the Telegram update and sends it to Builder.
3. Spark Intelligence Builder owns the turn: routing, memory context, personality/character, final answer composition, and safety boundaries.
4. `spark-voice-comms` owns speech I/O only:
   - `voice.transcribe` turns Telegram audio into text for Builder.
   - `voice.speak` turns Builder's final answer into audio.
   - `voice.status` reports current provider readiness.
   - `voice.onboard`, `voice.install`, and provider helpers guide setup.
5. Telegram bot sends the final text and, when voice is requested or enabled, the generated audio through Telegram voice delivery.

## Ownership Boundaries

- Builder owns the answer. Voice providers should not generate unrelated spoken content after Builder has already composed the reply.
- `spark-voice-comms` owns provider adapters and audio format details. It should not own memory, character, or long-term personality.
- `spark-character` owns persona artifacts and voice-surface style guidance. It can shape how Builder speaks, but it is not proof that TTS is configured.
- Spark Memory and wiki provide source-labeled context. They can influence the answer, but they do not prove that the current voice path works.
- Telegram owns delivery state. A successful `voice.speak` hook is not the same as a successful Telegram `sendVoice`.

## Current Working Controls

- `/voice` or `/voice status`: show current speech readiness from `voice.status`.
- `/probe voice`: record route evidence for Agent Operating Context using `voice.status`.
- `/voice map`: explain the stable runtime connections.
- `/voice dashboard` or `open voice dashboard`: write a redacted voice-system snapshot for Spawner UI and return the local `/voice-system` dashboard URL.
- `/voice provider`: show the current TTS provider for the Telegram DM.
- `switch my voice to ElevenLabs`, `use Kokoro for voice`, or `use GPT Realtime 2 for voice`: change the DM-level TTS provider preference.
- `find me a natural geeky QA tester voice`: search ElevenLabs voices from Telegram.
- `use voice Elise`: select an ElevenLabs voice by natural language.
- `make it warmer`, `a little faster`, `make it more geeky`: tune the selected ElevenLabs voice profile without triggering Spawner.
- `/voice undo`, `/voice rollback`, `undo that voice change`, or `go back to the previous voice`: restore the previous voice profile/provider for the current scope.
- `/voice ask <question>`: Builder generates an answer first, then speaks that answer.
- `/voice speak <text>`: read exact supplied text aloud.
- `/voice reply on` and `/voice reply off`: toggle automatic spoken replies for the current Telegram DM.

## Voice Preference Scoping

Voice preferences are identity state, not generic Telegram residue. They must be scoped tightly enough that one agent's voice tuning cannot leak into another agent.

Builder resolves TTS provider and voice profile state in this order:

1. Agent + Telegram profile + Telegram DM.
2. Telegram profile + Telegram DM.
3. Legacy Telegram DM-only state, only when the active Telegram profile is `default`.

This keeps existing default DM behavior working while protecting named profiles and future multi-agent deployments.

### State Key Shape

Current write targets:

- Provider preference: `telegram:voice_tts_provider:<scope>:<telegram_user_id>`.
- Voice profile preference: `telegram:voice_tts_profile:<scope>:<telegram_user_id>`.
- Previous profile/provider snapshot for rollback: `telegram:voice_tts_profile_undo:<scope>:<telegram_user_id>`.
- Legacy default-only fallback: `telegram:voice_tts_provider:<telegram_user_id>` and `telegram:voice_tts_profile:<telegram_user_id>`.

Scope examples:

- Default single-agent DM: legacy DM-only keys can still be read.
- Named Telegram profile without agent identity: `profile:parrotcovebird`.
- Agent-bound profile: `agent:agent-human-telegram-111:profile:default`.
- Agent-bound named profile: `agent:<agent-key>:profile:parrotcovebird`.

The scoped state payload should include a human-readable `scope` label so `/voice provider` and `/voice map` can explain where the preference lives.

### Parrot Cove Regression Class

The Parrot Cove voice profile uses the `parrotcovebird` Telegram profile, ElevenLabs TTS, and the `parrot` audio effect. It must not inherit generic ElevenLabs voice experiments such as Elise unless the operator explicitly tunes Parrot Cove while that profile is active.

Known failure mode:

1. A user tests a normal ElevenLabs voice in a default DM.
2. Builder saves the voice as `telegram:voice_tts_profile:<user_id>`.
3. A named agent/profile later reads that same DM-only key.
4. The named profile's character voice gets flattened into the generic voice.

Guardrail:

- Non-default Telegram profiles do not read legacy DM-only voice profile/provider keys.
- Agent-bound voice changes write to agent-scoped keys.
- Natural-language voice commands pass the active `agent_id` into provider selection and voice mutation.

## Provider Reality

- ElevenLabs is the polished hosted TTS path and the main path for natural voice calibration.
- Kokoro is the private/free local neural TTS path once model and voice files are connected locally.
- GPT Realtime 2 is a hosted OpenAI voice option and can be useful for OpenAI-native setups, but it is not assumed to be the default personality voice.
- OpenAI-compatible STT can be used explicitly. Local faster-whisper remains the preferred private transcription path when available.
- MiniMax and Z.ai/GLM should be treated as explicit adapters when implemented and verified, not guessed from their chat-provider availability.
- Codex CLI is an execution provider, not a voice STT/TTS provider.

## Self-Awareness Rules

Spark should only claim voice is ready when current evidence supports the exact claim:

- Attached chip: `spark-voice-comms` is visible and active or callable.
- STT readiness: `voice.status` reports transcription readiness for the selected path.
- TTS readiness: the selected provider has local config references and a successful synthesis test.
- Telegram delivery: a real voice message was delivered, not merely synthesized.
- Reply behavior: `/voice reply on/off` is per Telegram DM and separate from provider readiness.

If these disagree, Spark should say what is ready and what still needs proof.

## Production Checks

Before claiming voice is production-ready for a new user or deployment, verify:

- `/voice` reports the intended chip and provider readiness.
- `/voice provider` shows the expected provider and preference scope.
- `/voice map` describes the Telegram, Builder, `spark-voice-comms`, memory, and delivery boundaries.
- `/voice dashboard` opens the Spawner UI voice-system page using redacted runtime evidence only.
- `/voice-system` reads live Builder runtime state for the current provider/profile and last Telegram delivery proof, so `/voice dashboard` is no longer required after every voice reply just to refresh status.
- A real Telegram voice note transcribes through the selected STT path.
- `/voice ask <question>` generates a Builder answer first and speaks that answer, rather than reading the prompt back.
- `/voice speak <text>` reads exact supplied text and is labeled as exact-read behavior.
- `/voice reply on` produces a matching text/audio answer pair for normal messages.
- Switching one agent's voice does not change another agent or named profile.
- Provider keys are never pasted into Telegram, logs, docs, captions, or runtime state.

Regression tests currently cover:

- Named profiles ignoring default DM voice tuning.
- ElevenLabs selection writing to agent-scoped profile state.
- Natural-language voice mutation preserving scoped profile state.
- DM provider state overriding env provider defaults when the same agent scope is active.
- Voice runtime delivery evidence after Telegram `sendVoice`.
- `/voice` rendering the same scoped profile and delivery proof as the other voice surfaces.
- Scoped voice rollback after natural-language tuning.
- Friendly ElevenLabs credential failure copy without raw provider JSON in Telegram-facing replies.

## Security Boundary

Secrets stay in local config or Spark's secret layer. Telegram onboarding can name required environment variable names, but it must not ask users to paste API keys into chat. Status and diagnostics should report `present`, `missing`, or masked IDs only.

The voice-system dashboard follows the same rule. Builder writes only a redacted snapshot: provider labels, masked voice IDs, readiness flags, runtime ownership, Telegram delivery status, and safe command hints. Provider keys, Telegram tokens, raw local env values, and private account identifiers must not be included in the snapshot.

## Rollback

If scoped voice state causes confusion in a deployment:

1. Leave registry profiles intact.
2. Clear only the bad scoped key for the affected user/profile/agent.
3. Rerun `/voice provider` and `/voice map`.
4. Test one `/voice ask` and one normal reply with `/voice reply on`.

Do not delete the whole runtime state database to fix one voice preference.
