# Security

Spark Intelligence Builder is the runtime core behind the Telegram gateway. In launch v1 it should not own the live Telegram bot token.

## Launch Boundaries

- `spark-telegram-bot` owns Telegram long polling.
- Builder owns runtime state, provider profiles, memory bridge behavior, adapter logic, and domain-chip activation.
- Builder may receive Telegram-shaped updates through the bridge, but it should not start a competing Telegram receiver for the same bot token.
- Cloud provider secrets should be configured through Builder auth commands or Spark CLI secret storage, not committed env files.

## Files That Must Never Be Committed

- `.env`, `.env.*`
- local `config.yaml`, `state.db`, logs, runtime homes, and generated artifacts containing user data
- Telegram bot tokens
- LLM API keys
- OAuth tokens or refresh tokens

## Launch Checks

Run:

```bash
python -m pytest tests/test_secret_file_permissions.py tests/test_gateway_discord_webhook.py -q
spark-intelligence doctor
```

Before public demos, rotate any token that appeared in chat, logs, screenshots, or issue text.
