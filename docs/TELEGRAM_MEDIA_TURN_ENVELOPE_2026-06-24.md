# Telegram Media Turn Envelope

Date: 2026-06-24
Status: Builder gateway acceptance note

## Purpose

Telegram can send non-text turns as `spark.media_turn.v1` metadata. Builder should preserve the safe envelope so media handling can be traced without storing raw files, file ids, audio bytes, transcript bodies, or provider secrets in gateway traces.

## Accepted Shape

Builder accepts a `spark_media_turn` object on either the update root or `message`.

The gateway keeps only:

- `schema`
- `media_kind`
- `chat_surface`
- valid redacted `turn_ref`
- bounded `caption_text`
- `analysis_policy`
- `authority`
- safe source booleans such as `has_photo`, `has_voice`, `has_audio`, `has_document`
- `mime_family`
- `filename_present`

It does not preserve raw `file_id`, raw filenames, audio bytes, transcript bodies, or executable instructions from media.

## Current Behavior

- Text, voice, audio, photo, and document updates normalize as supported Telegram turns.
- Captioned photo/document turns use the caption as the effective text.
- Captionless photo/document turns normalize to a placeholder such as `[photo message]` or `[document message]`.
- Gateway simulation detail and gateway trace rows include the cleaned `media_turn` when present.

## Boundary

The media envelope is evidence, not authority. Fresh Harness/TurnIntent still decides whether Builder may analyze media, transcribe audio, write memory, launch work, or perform any stronger action.
