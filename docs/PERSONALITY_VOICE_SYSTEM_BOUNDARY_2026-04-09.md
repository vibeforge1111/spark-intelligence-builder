# Personality, Builder, And Voice Boundary

This note defines how the current live system is split across:

- `spark-personality-chip-labs`
- `spark-intelligence-builder`
- `domain-chip-voice-comms`

It exists to stop the same question from recurring: where does the agent's personality actually live, and how does voice relate to it?

## Ownership

### `spark-personality-chip-labs`

This repo is the source and import seam for baseline persona.

It should own:

- personality chip files
- chip-side personality schema and validation
- the active personality hook
- conversion from chip identity/voice/tone into a Builder import payload

It should not own:

- live Telegram style training
- Builder-side style presets
- Builder-side savepoints or undo history
- voice transcription or voice reply transport

Current relevant hook:

- `personality`

### `spark-intelligence-builder`

This repo is the live runtime owner of conversational personality.

After personality is imported, Builder should own:

- the current saved agent persona
- Telegram-visible style
- style training and feedback
- style presets
- style scoring, examples, and compare surfaces
- style undo
- style savepoints and restore

Those are Builder-native systems. They are not written back into the personality chip repo during normal Telegram use.

Builder persistence is local and immediate. The next Telegram reply reads the updated Builder persona state.

### `domain-chip-voice-comms`

This repo is the speech I/O layer.

It should own:

- STT provider logic
- TTS provider logic
- voice profile definitions
- provider compatibility logic
- voice fallback behavior
- voice evaluation and tuning

It should not own the conversational personality itself.

The voice chip should preserve the Builder-owned personality by operating around it, not replacing it.

## Runtime Flow

### Text path

1. Telegram message arrives in Builder.
2. Builder resolves the DM identity and Builder-local agent id.
3. Builder loads the saved Builder persona.
4. Builder generates or shapes the reply using that live persona state.
5. Builder sends text back to Telegram.

### Voice-input path

1. Telegram voice/audio message arrives in Builder.
2. Builder fetches Telegram media bytes.
3. Builder calls `domain-chip-voice-comms` via `voice.transcribe`.
4. The transcript re-enters the same Builder conversation runtime as text.
5. Builder loads the same saved Builder persona and produces the reply.

### Voice-output path

1. Builder decides to send a voice reply.
2. Builder passes final reply text into `domain-chip-voice-comms` via `voice.speak`.
3. For Telegram, the voice chip should synthesize Telegram-friendly Opus voice-note audio rather than generic MP3 output.
4. Builder should deliver Telegram-compatible voice-note media with `sendVoice`, not a generic document/audio fallback path.
5. Telegram receives the returned audio artifact back as a voice note.

That transport detail matters. The old MP3-style path produced different playback behavior and was not equivalent to the older Openclaw Telegram setup.

The important point is that the voice chip handles conversion, not personality authorship.

## What Is Builder-Native Right Now

These systems are directly attached to the live Builder persona state:

- `/style train`
- `/style feedback`
- `/style good`
- `/style bad`
- `/style presets`
- `/style preset <name>`
- `/style score`
- `/style examples`
- `/style compare`
- `/style before-after <instruction>`
- `/style undo`
- `/style savepoint <name>`
- `/style savepoints`
- `/style restore <name>`

Natural-language equivalents for those routes are also Builder-native.

## What This Means Operationally

- Importing a personality chip seeds Builder with a base persona.
- Telegram training evolves Builder's local persona state, not the chip file.
- Voice replies should sound like the same agent because Builder owns the live persona and the voice chip only renders it.
- `spark-swarm` can own shared intelligence, coordination, and external identity, but not the visible local conversational personality.

## Current Rule

Use this split unless there is a strong reason to change it:

- personality chip = portable source persona
- Builder = living conversational personality
- voice chip = speech transport and rendering around Builder personality
