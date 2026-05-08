# Telegram Command Reference 2026-04-09

This is the current Builder-owned Telegram control surface.

The rule is:

- explicit slash commands stay the canonical operator interface
- bounded natural-language equivalents are supported where the intent is clear
- natural-language capture should map onto the same internal command path, not create a second behavior system

## Swarm

- `/swarm`
- `/swarm status`
- `/swarm overview`
- `/swarm live`
- `/swarm runtime`
- `/swarm specializations`
- `/swarm insights [specialization]`
- `/swarm masteries [specialization]`
- `/swarm upgrades [specialization]`
- `/swarm issues`
- `/swarm inbox`
- `/swarm collective`
- `/swarm sync`
- `/swarm paths`
- `/swarm run <path_key>`
- `/swarm autoloop <path_key> [rounds <n>]`
- `/swarm continue <path_key> [session <id>] [rounds <n>]`
- `/swarm sessions <path_key>`
- `/swarm session <path_key> [latest|<session_id>]`
- `/swarm rerun [path_key]`
- `/swarm evaluate <task>`
- `/swarm absorb <insight_id> [because <reason>]`
- `/swarm review <mastery_id> <approve|defer|reject> [because <reason>]`
- `/swarm mode <specialization_id> <observe_only|review_required|checked_auto_merge|trusted_auto_apply>`
- `/swarm deliver <upgrade_id>`
- `/swarm sync-delivery <upgrade_id>`

Natural-language examples:

- `Can you show me the swarm status?`
- `What upgrades are pending in swarm?`
- `Please sync with swarm`
- `Can you evaluate this for swarm: <task>`
- `Show me Startup Operator insights in swarm`
- `Start autoloop for Startup Operator in swarm for 2 rounds`

## Chips

- `/chip`
- `/chip status [chip_key]`
- `/chip evaluate <chip_key> [text|key=value ...|json]`
- `/chip suggest <chip_key> [text|key=value ...|json]`
- `/chip autoloop <chip_key>`

Examples:

- `/chip status`
- `/chip status domain-chip-trading-crypto`
- `/chip evaluate domain-chip-trading-crypto doctrine_id=trend_regime_following strategy_id=ema_pullback_long market_regime=trend timeframe=1h venue=binance asset_universe=BTC paper_gate=strict`
- `/chip suggest domain-chip-trading-crypto {"limit":2}`
- `/chip autoloop domain-chip-trading-crypto`

Current behavior:

- direct chip commands execute the chip hook locally through the existing attachment contract
- direct chip commands do not automatically create Swarm insights, Swarm autoloop sessions, or GitHub delivery records
- `/chip autoloop <chip_key>` is intentionally explanatory only; true autoloop remains specialization-path-owned through `/swarm autoloop <path_key>`

## Style

- `/style`
- `/style status`
- `/style history`
- `/style savepoints`
- `/style savepoint <name>`
- `/style diff <name>`
- `/style restore <name>`
- `/style presets`
- `/style preset <name>`
- `/style undo`
- `/style score`
- `/style examples`
- `/style compare`
- `/style before-after <instruction>`
- `/style test`
- `/style train <instruction>`
- `/style feedback <note>`
- `/style good <note>`
- `/style bad <note>`

Natural-language examples:

- `Can you show me my current style?`
- `What style changes have you saved?`
- `What style savepoints do I have?`
- `Save style savepoint named checkpoint one`
- `Compare my style to savepoint checkpoint one`
- `Restore style savepoint named checkpoint one`
- `What style presets are available?`
- `Set style preset to claude-like`
- `Undo the last style change`
- `Score my style`
- `Show me my style examples`
- `Compare my style`
- `Show me style before and after for be more direct and keep replies short`
- `Train your style to be more direct and keep replies short`
- `Be more Claude-like in conversation continuity`
- `That was too verbose`
- `Less canned and more grounded follow-up questions`

Current natural-language style capture is intentionally bounded to explicit style/personality requests and clear reply-quality feedback. It should not try to reinterpret arbitrary conversation as style mutation.

Recommended live workflow:

- use the agent normally and save style feedback from real exchanges
- prefer concrete notes like `too polished`, `too generic`, or `ask fewer follow-up questions`
- avoid synthetic memory-probe loops unless you are isolating a runtime bug

## Voice

- `/voice`
- `/voice plan`
- `/voice reply`
- `/voice reply status`
- `/voice reply on`
- `/voice reply off`
- `/voice ask <question>`
- `/voice speak <text>`

Natural-language examples:

- `What is the voice status?`
- `How does voice work?`
- `Turn voice replies on`
- `Turn voice replies off`
- `Answer this in voice: <question>`
- `Please speak this out loud: <text>`
- `Send this as voice: <text>`

Current live behavior:

- Telegram voice and audio messages are transcribed through `domain-chip-voice-comms`
- voice-origin Telegram turns auto-reply with audio when TTS succeeds, even if `/voice reply on` is not set
- `/voice reply on` enables automatic audio replies for later text-origin turns in that DM
- `/voice ask <question>` generates an answer first, then sends that answer as audio
- `/voice speak <text>` reads the provided text exactly; use it for scripts, not generated answers
- Builder keeps the normal Telegram caption text, but sends a voice-shaped spoken variant into `voice.speak` so spoken replies stay shorter and cleaner
- Telegram voice replies should be synthesized in a Telegram-friendly Opus voice-note format and delivered with `sendVoice`
- do not silently fall back to generic MP3/document delivery unless you are intentionally accepting different playback behavior

### Profile Voice Overrides

Telegram profiles can override voice delivery without changing Builder's global voice provider. The supported profile env keys are:

- `SPARK_TELEGRAM_VOICE_PROFILE_REGISTRY`
- `SPARK_TELEGRAM_VOICE_TTS_PROVIDER`
- `SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_VOICE_ID`
- `SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_VOICE_NAME`
- `SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_MODEL_ID`
- `SPARK_TELEGRAM_VOICE_TTS_STABILITY`
- `SPARK_TELEGRAM_VOICE_TTS_SIMILARITY_BOOST`
- `SPARK_TELEGRAM_VOICE_TTS_STYLE`
- `SPARK_TELEGRAM_VOICE_TTS_SPEED`
- `SPARK_TELEGRAM_VOICE_TTS_USE_SPEAKER_BOOST`
- `SPARK_TELEGRAM_VOICE_AUDIO_EFFECT`

`SPARK_TELEGRAM_VOICE_AUDIO_EFFECT=parrot` applies the balanced Parrot Cove Bird filter after TTS and before Telegram delivery. Keep this profile-scoped so other Telegram bots do not inherit the character voice.

By default, Builder looks for a profile voice registry at `~/.spark/config/telegram-voice-profiles.json`. `SPARK_TELEGRAM_VOICE_PROFILE_REGISTRY` can point at a different file. Env vars override registry values for emergency rollback or local experiments.

Registry shape:

```json
{
  "profiles": {
    "parrotcovebird": {
      "provider_id": "elevenlabs",
      "voice_id": "ZWw77cKDlDtiE9JYM1Wq",
      "voice_name": "Parrot Cove Bird",
      "model_id": "eleven_turbo_v2_5",
      "voice_settings": {
        "stability": 0.48,
        "similarity_boost": 0.70,
        "style": 0.44,
        "speed": 1.06,
        "use_speaker_boost": false
      },
      "audio_effect": "parrot"
    }
  }
}
```

`/voice` and `/voice status` append the active profile voice summary when a registry or profile env override is present. Voice IDs are masked in the status reply.

Current Parrot Cove Bird recipe:

- ElevenLabs voice: `Parrot Cove Bird`
- voice id: `ZWw77cKDlDtiE9JYM1Wq`
- model: `eleven_turbo_v2_5`
- settings: `stability=0.48`, `similarity_boost=0.70`, `style=0.44`, `speed=1.06`, `use_speaker_boost=false`
- effect: `parrot`

Rollback path:

- remove `SPARK_TELEGRAM_VOICE_AUDIO_EFFECT` to keep ElevenLabs voice output without the parrot filter
- remove the profile-level `SPARK_TELEGRAM_VOICE_TTS_*` values to fall back to Builder's global voice provider

## Think Visibility

- `/think`
- `/think on`
- `/think off`

Natural-language examples:

- `What is the thinking status?`
- `Turn thinking on`
- `Turn thinking off`

## Implementation Rule

When adding a new Telegram runtime command:

1. Add the explicit slash command first.
2. Add bounded natural-language equivalents only when intent can be recognized reliably.
3. Route natural-language capture into the same internal command handler.
4. Add simulation tests for both slash and natural-language forms.
5. Update this file and the README command section in the same change.
