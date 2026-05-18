# Spark Intelligence Builder — Bug Audit Report

## Methodology
All Python source files under `src/spark_intelligence/` were read and analyzed. Bugs are categorized by severity and include file path, line number, description, expected vs actual behavior, and a concrete fix.

---

## BUG 1 — OAuth tokens stored in plaintext (HIGH)

**File:** `src/spark_intelligence/auth/service.py:793-804`

**Description:** The `_persist_oauth_tokens` function stores `access_token` and `refresh_token` directly into the `oauth_credentials` table under columns named `access_token_ciphertext` and `refresh_token_ciphertext`. Despite the column names implying encryption, the values are stored as raw plaintext — `str(token_payload.get("access_token"))` is written directly with no encryption step. This is misleading and insecure.

**Expected behavior:** Column names with `_ciphertext` suffix should contain encrypted data, or the code should document why plaintext is acceptable and rename the columns.

**Actual behavior:** Tokens are stored in cleartext. The column naming gives a false sense of security.

**Fix:**
```python
# Option A: Add actual encryption (preferred for production)
# Use a local encryption key derived from a machine secret:
from spark_intelligence.security.encryption import encrypt_value, decrypt_value

# In _persist_oauth_tokens, line ~800:
access_token_encrypted = encrypt_value(str(token_payload.get("access_token")))
refresh_token_encrypted = encrypt_value(str(token_payload.get("refresh_token"))) if token_payload.get("refresh_token") else None

# Option B (minimal): At minimum, add a code comment and a runtime warning
# and rename the columns if encryption is deferred:
# TODO(security): Tokens are stored in cleartext despite _ciphertext column names.
# This must be addressed before multi-user deployment.
```

---

## BUG 2 — `normalize_runtime_path` WSL/Windows cross-check has inverted OS condition (HIGH)

**File:** `src/spark_intelligence/config/loader.py:161-174`

**Description:** The `normalize_runtime_path` method checks for WSL paths (`/mnt/X/...`) and Windows paths (`C:\...`). However, the WSL-to-Windows translation is gated on `os.name == "nt"` (i.e., running on Windows), and the Windows-to-WSL translation is gated on `os.name != "nt"` (i.e., running on Linux/macOS).

The logic is:
- Line 162: `if wsl_match and os.name == "nt"` — tries to convert `/mnt/c/...` to `C:\...` when running on Windows
- Line 169: `if windows_match and os.name != "nt"` — tries to convert `C:\...` to `/mnt/c/...` when running on Linux

This is **inverted**. A WSL path (`/mnt/c/...`) would only appear when the config was created under WSL (Linux), and needs to be translated when running on native Windows (`os.name == "nt"`). That part is correct. But the reverse — a Windows path in config needing translation to WSL — would only happen when running under WSL Linux, which reports `os.name == "posix"`, so `os.name != "nt"` is `True`. That is also correct.

Wait, on second analysis: when running under WSL, `os.name` is `"posix"`, not `"nt"`. So `os.name != "nt"` is `True`, and the Windows-to-WSL path translation fires. This is correct. And when running on native Windows, `os.name == "nt"`, the WSL-to-Windows translation fires. This is also correct for the case where someone shares a config between WSL and native Windows.

However, there is a real bug: **when running on native Linux (not WSL)**, `os.name != "nt"` is `True`, so a config containing a Windows path like `C:\Users\...` would be incorrectly translated to `/mnt/c/Users/...` on a system that is not WSL. This path won't exist on native Linux, and the fallback on line 175 returns the raw `Path(raw)` which is the original Windows path — also wrong.

**Expected behavior:** Windows-to-WSL path translation should only happen when actually running under WSL, not on all non-Windows systems.

**Actual behavior:** Native Linux systems will attempt (and fail) WSL path translation for any Windows-style paths.

**Fix:**
```python
# Replace line 169:
if windows_match and os.name != "nt":

# With a WSL-specific check:
if windows_match and os.name != "nt" and _is_wsl():

# Add helper:
@staticmethod
def _is_wsl() -> bool:
    """Check if running under Windows Subsystem for Linux."""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except (OSError, AttributeError):
        return False
```

---

## BUG 3 — `_record_config_mutation` silently swallows all exceptions (MEDIUM)

**File:** `src/spark_intelligence/config/loader.py:502-503`

**Description:** The `_record_config_mutation` method catches all exceptions with a bare `except Exception: return`. This means if the StateDB is corrupt, the schema hasn't been initialized, or there's a programming error in the mutation recording logic, it silently fails with no logging. This makes debugging nearly impossible — config changes succeed but no audit trail is recorded, and there's no indication anything went wrong.

**Expected behavior:** Config mutation audit failures should be logged or surfaced, not silently discarded.

**Actual behavior:** Any failure in mutation recording is completely invisible.

**Fix:**
```python
# Replace lines 501-503:
except Exception:
    return

# With:
except Exception as exc:
    import logging
    logging.getLogger(__name__).warning(
        "Failed to record config mutation for %s/%s: %s",
        target_document, target_path, exc,
    )
    return
```

---

## BUG 4 — `_diagnostic_status` returns wrong result when only `finding_signatures` is non-zero (MEDIUM)

**File:** `src/spark_intelligence/context/capsule.py:557-564`

**Description:** The `_diagnostic_status` function has a logic error:

```python
def _diagnostic_status(summary: dict[str, str]) -> str:
    failure_lines = _parse_int(summary.get("failure_lines"))
    finding_signatures = _parse_int(summary.get("finding_signatures"))
    if failure_lines == 0 and finding_signatures == 0:
        return "clean_latest_scan_no_failures_or_findings"
    if failure_lines is not None or finding_signatures is not None:
        return "latest_scan_has_findings"
    return ""
```

The second condition `if failure_lines is not None or finding_signatures is not None:` is almost always true because `_parse_int` returns `int | None` and if either parsed successfully, the condition triggers. But consider the case where `failure_lines` is 5 and `finding_signatures` is `None` (couldn't parse). The first condition fails (5 != 0), and the second condition `5 is not None or None is not None` → `True or False` → `True`, so it returns `"latest_scan_has_findings"`. This happens to be correct.

But what about `failure_lines=0` and `finding_signatures=None`? The first condition `0 == 0 and None == 0` → `False`, second condition `0 is not None or None is not None` → `True or False` → `True`, returns `"latest_scan_has_findings"`. This is **wrong** — there are 0 failure lines and no finding signatures data, so the status should be `"clean_latest_scan_no_failures_or_findings"`.

**Expected behavior:** `failure_lines=0` with `finding_signatures=None` should return `"clean_latest_scan_no_failures_or_findings"`.

**Actual behavior:** Returns `"latest_scan_has_findings"`.

**Fix:**
```python
def _diagnostic_status(summary: dict[str, str]) -> str:
    failure_lines = _parse_int(summary.get("failure_lines"))
    finding_signatures = _parse_int(summary.get("finding_signatures"))
    has_failures = failure_lines is not None and failure_lines > 0
    has_findings = finding_signatures is not None and finding_signatures > 0
    if not has_failures and not has_findings:
        return "clean_latest_scan_no_failures_or_findings"
    return "latest_scan_has_findings"
```

---

## BUG 5 — `mask_secret` leaks first 6 and last 4 characters of short secrets (MEDIUM)

**File:** `src/spark_intelligence/security/redaction.py:42-45`

**Description:** The `mask_secret` function returns `***` for values shorter than 18 characters, but for values 18+ characters it returns `{value[:6]}...{value[-4:]}`. This means a 17-character API key would be fully masked, but an 18-character key would expose its first 6 and last 4 characters. Many real API keys (e.g., GitHub personal access tokens `github_pat_...`) are 18-40 characters, so their prefix and suffix would be partially exposed.

Additionally, this function is used in the redaction patterns for assignment and JSON field matches, meaning partial key material could appear in logs.

**Expected behavior:** Secret values should be consistently redacted regardless of length.

**Actual behavior:** Secrets >= 18 chars have their first 6 and last 4 characters exposed in logs.

**Fix:**
```python
def mask_secret(value: str) -> str:
    if len(value) < 8:
        return "***"
    return f"{value[:3]}...{value[-2:]}"
    # Or more conservatively: return "***REDACTED***"
```

---

## BUG 6 — WhatsApp webhook signature validation uses lowercase `hmac.new` instead of `hmac.new` (HIGH)

**File:** `src/spark-intelligence-builder/src/spark_intelligence/gateway/whatsapp_webhook.py:384`

**Description:** Line 384 uses `hmac.new(...)` instead of `hmac.new(...)`. Python's `hmac` module has `hmac.new()` as the constructor function. `hmac.new` does not exist — it would be `hmac.HMAC` or `hmac.new()`. However, looking at the import on line 4: `import hmac`, the correct call is `hmac.new()`. The code on line 384 says:

```python
expected_signature = "sha256=" + hmac.new(
```

This should be `hmac.new` (capital N). Wait — in Python, the function is `hmac.new()`. Let me re-check. The `hmac` module provides `hmac.new(key, msg, digestmod)`. So `hmac.new` is correct.

Actually, re-reading the code at line 384: `hmac.new(` — this IS correct Python. The `hmac.new()` function exists. My mistake, this is not a bug.

---

## BUG 6 (revised) — WhatsApp webhook records raw `delivered_text` with secrets in event log (MEDIUM)

**File:** `src/spark_intelligence/gateway/whatsapp_webhook.py:432-433`

**Description:** The `_record_whatsapp_delivery` function stores `delivered_text` directly in the `facts` dict that gets persisted to `builder_events` via `record_event`. While the outbound text has been through `prepare_outbound_text`, which includes redaction, the raw_text and delivered_text are still stored verbatim in the database event log. If the redaction step misses something (which is possible given the regex-based approach), sensitive material ends up in the SQLite database.

The same pattern exists in `discord_webhook.py:514`.

**Expected behavior:** Sensitive material should be redacted before database persistence, or a secondary redaction pass should be applied to stored facts.

**Actual behavior:** Full message text (including any redaction misses) is persisted in the event log database.

**Fix:**
```python
# In _record_whatsapp_delivery and _record_discord_delivery, apply redaction:
from spark_intelligence.security.redaction import redact_text

facts = {
    ...
    "delivered_text": redact_text(delivered_text),
    ...
}
```

---

## BUG 7 — Provider routing: `openai-codex` provider has unsupported `api_mode` with no guard (HIGH)

**File:** `src/spark_intelligence/auth/providers.py:49-61`

**Description:** The `openai-codex` provider is registered with `api_mode="codex_responses"` and `execution_transport="external_cli_wrapper"`. However, in `direct_provider.py:92-95`, the `execute_direct_provider_prompt` function only supports `"chat_completions"` and `"anthropic_messages"` — any other `api_mode` raises `RuntimeError`.

The `execution_transport="external_cli_wrapper"` means the code should go through the researcher bridge wrapper, not the direct provider. But there's no guard preventing someone from calling `execute_direct_provider_prompt` with the `openai-codex` provider. If the routing logic ever falls through to the direct provider path, it will fail with an unhelpful error.

**Expected behavior:** Either the `codex_responses` api_mode should be handled, or the provider should be explicitly excluded from direct execution with a clear error message.

**Actual behavior:** An opaque `RuntimeError` about "unsupported direct execution mode" is raised.

**Fix:**
```python
# In direct_provider.py, add a pre-check after line 76:
if provider.api_mode not in ("chat_completions", "anthropic_messages"):
    raise RuntimeError(
        f"Provider '{provider.provider_id}' uses api_mode '{provider.api_mode}' "
        f"which requires the researcher bridge wrapper, not direct HTTP execution. "
        f"Check the provider's execution_transport configuration."
    )
```

---

## BUG 8 — `_read_jsonl_tail` reads entire file into memory (MEDIUM)

**File:** `src/spark_intelligence/context/recent_conversation.py:244`

**Description:** The `_read_jsonl_tail` function reads the entire JSONL file into memory with `path.read_text(encoding="utf-8").splitlines()` and then slices the last N lines. For long-running systems, the gateway trace and outbound log files can grow very large (tens of MB or more). This causes excessive memory usage every time recent conversation turns are loaded.

**Expected behavior:** Only the tail of the file should be read, not the entire file.

**Actual behavior:** The full file is loaded into memory on every context capsule build.

**Fix:**
```python
def _read_jsonl_tail(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        if not path.exists():
            return []
        # Read only the tail of the file using seek from the end
        with path.open("rb") as f:
            if f.seek(0, 2) == 0:  # empty file
                return []
            # Read last ~64KB which should contain enough lines
            read_size = min(f.tell(), 65536 * max(limit, 1) // 20 + 65536)
            f.seek(max(0, f.tell() - read_size))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
            if read_size < f.seek(0, 2) and lines:
                lines = lines[1:]  # drop partial first line
    except OSError:
        return []
    selected = lines[-limit:] if limit > 0 else lines
    records: list[dict[str, Any]] = []
    for line in selected:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records
```

---

## BUG 9 — `read_gateway_traces` and `read_outbound_audit` don't handle malformed JSON (LOW)

**File:** `src/spark_intelligence/gateway/tracing.py:54`

**Description:** Unlike `_read_jsonl_tail` in `recent_conversation.py` which gracefully handles `json.JSONDecodeError`, the `read_gateway_traces` and `read_outbound_audit` functions call `json.loads(line)` without a try/except. A single corrupted line in the JSONL file will cause the entire trace read to fail with an unhandled exception.

**Expected behavior:** Malformed lines should be skipped, consistent with the pattern in `recent_conversation.py`.

**Actual behavior:** A single bad line crashes the trace reader.

**Fix:**
```python
# In read_gateway_traces, replace line 54:
traces.append(json.loads(line))

# With:
try:
    record = json.loads(line)
    if isinstance(record, dict):
        traces.append(record)
except json.JSONDecodeError:
    continue

# Same fix for read_outbound_audit at line 68.
```

---

## BUG 10 — Em-dash replacement inserts extra spaces around hyphens (MEDIUM)

**File:** `src/spark_intelligence/gateway/guardrails.py:229-235`

**Description:** The `_strip_em_dashes` function replaces each em-dash character with `" - "` (space-hyphen-space). This means the text `"word—word"` (single em-dash) becomes `"word - word"`, which is correct. But `"word—"` (em-dash at end) becomes `"word - "`, and `"—word"` becomes `" - word"`. More importantly, the code then collapses double spaces but not the extra single spaces introduced around hyphens that were already adjacent to spaces, like `"word — word"` which becomes `"word  -  word"` → `"word - word"` (correct after double-space collapse). However, for `"word —word"`, it becomes `"word  - word"` which is fine.

The real problem is that legitimate hyphenated identifiers like `"some-key-name"` are **not** affected (since they use regular hyphens). But the code replaces ALL em-dash variants including `\u2212` (MINUS SIGN), which is a mathematical operator that might legitimately appear in numerical contexts. Replacing it with `" - "` (space-hyphen-space) could alter the semantic meaning of mathematical expressions.

**Expected behavior:** Mathematical minus signs should be preserved or converted to a plain hyphen without surrounding spaces.

**Actual behavior:** The MINUS SIGN character (U+2212) is replaced with ` - ` which breaks mathematical expressions.

**Fix:**
```python
def _strip_em_dashes(text: str) -> str:
    if not text:
        return text
    out = text
    # Replace actual dash-like characters with hyphen
    for ch in ("\u2014", "\u2013", "\u2012", "\u2015"):
        out = out.replace(ch, "-")
    # Replace MINUS SIGN with plain hyphen (no extra spaces)
    out = out.replace("\u2212", "-")
    # Clean up space-hyphen-space patterns that look right
    # but don't add spaces around hyphens that are already next to non-space chars
    while "  " in out:
        out = out.replace("  ", " ")
    return out
```

---

## BUG 11 — `connect_provider` allows setting provider without any API key (MEDIUM)

**File:** `src/spark_intelligence/auth/service.py:148-232`

**Description:** The `connect_provider` function allows creating a provider with no API key at all. When `api_key` is `None` and `api_key_env` points to an env var that doesn't exist, the provider is still registered with `status="pending_secret"`. However, the code then adds this provider to `config["providers"]["records"]` and sets it as the `default_provider` if none exists (line 177). A provider with `status="pending_secret"` becomes the default, and subsequent requests to use the provider will fail at runtime rather than at configuration time.

**Expected behavior:** A provider without a valid API key should not be set as the default provider.

**Actual behavior:** An unauthenticated provider can become the default, causing runtime failures.

**Fix:**
```python
# In connect_provider, after line 176:
if not config["providers"].get("default_provider"):
    config["providers"]["default_provider"] = provider

# Change to:
if not config["providers"].get("default_provider") and profile_status == "active":
    config["providers"]["default_provider"] = provider
```

---

## BUG 12 — Race condition in `consume_oauth_callback_state` (MEDIUM)

**File:** `src/spark_intelligence/auth/oauth_state.py:83-133`

**Description:** The `consume_oauth_callback_state` function reads the OAuth state, validates it, and then updates it — all in a single SQLite transaction (which is correct since `with state_db.connect()` is used). However, between the read (line 94) and the status update (line 123), there's a validation check on line 108: `if _parse_timestamp(record.expires_at) <= now`. This check uses `now = datetime.now(UTC)` captured at line 91.

The problem is that `now` is captured **before** the SELECT query. If the function takes a long time (e.g., due to slow I/O), the state could have expired between `now` being set and the actual consumption. More critically, if two concurrent requests arrive for the same OAuth state, SQLite's default transaction mode should prevent the race, but the code doesn't use `BEGIN IMMEDIATE` to acquire a write lock early, so two concurrent readers could both pass the validation before either writes.

**Expected behavior:** OAuth state consumption should be atomic with write-lock acquisition at the start.

**Actual behavior:** Under concurrent access, both requests could pass validation before either commits.

**Fix:**
```python
# After opening the connection, execute an immediate transaction:
with state_db.connect() as conn:
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(...)
    # ... rest of the function
```

---

## BUG 13 — `run_governed_command` doesn't validate input (MEDIUM)

**File:** `src/spark_intelligence/execution/governed.py:26-47`

**Description:** The `run_governed_command` function passes `command` directly to `subprocess.run` without any validation. A malicious or misconfigured caller could inject arbitrary commands (e.g., shell injection through arguments). The function doesn't validate that:
1. `command` is a non-empty list
2. The executable path is within allowed boundaries
3. Arguments don't contain dangerous patterns

While the function is called internally, the name "governed" implies security controls that don't actually exist.

**Expected behavior:** A "governed" command execution should validate the command against an allowlist or at least basic safety checks.

**Actual behavior:** Any command list is executed without validation.

**Fix:**
```python
def run_governed_command(
    *,
    command: list[str],
    cwd: str | Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> GovernedCommandExecution:
    if not command:
        raise ValueError("Governed command must not be empty.")
    if not command[0]:
        raise ValueError("Governed command executable must not be empty.")
    if not str(cwd).strip():
        raise ValueError("Governed command cwd must not be empty.")
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    # ... rest unchanged
```

---

## BUG 14 — `_extract_access_value` returns `"5"` for "full access" but access levels are 1-4 (LOW)

**File:** `src/spark_intelligence/context/harness.py:447-455`

**Description:** The `_extract_access_value` function returns `"5"` when "full access" is detected in text. However, the access level system described in the codebase uses levels 1-4 (as shown by the `_ACCESS_VALUE_RE` pattern matching `[1-4]`). Returning `"5"` creates an invalid access level that doesn't correspond to any defined level.

**Expected behavior:** "Full access" should map to a valid access level (likely `"4"` as the highest defined level).

**Actual behavior:** Returns `"5"` which is outside the valid range.

**Fix:**
```python
# Line 450, change:
if "full access" in lowered:
    return "5"

# To:
if "full access" in lowered:
    return "4"
```

---

## BUG 15 — `_upsert_oauth_provider_record` sets `api_key_env` to `None` for OAuth providers, breaking reconnect (MEDIUM)

**File:** `src/spark_intelligence/auth/service.py:701-708`

**Description:** When `_upsert_oauth_provider_record` updates the config for an OAuth provider, it explicitly sets `api_key_env` to `None` (line 705: `"api_key_env": None`). This means that if a user first connects a provider with an API key and then does an OAuth login for the same provider, the API key environment variable reference is erased from the config. The user can no longer fall back to API key auth if the OAuth flow has issues.

**Expected behavior:** OAuth providers should either preserve existing `api_key_env` or not overwrite it with `None`.

**Actual behavior:** OAuth login overwrites `api_key_env` to `None`, losing the API key reference.

**Fix:**
```python
# In _upsert_oauth_provider_record, line 705, change:
"api_key_env": None,

# To:
"api_key_env": existing_record.get("api_key_env"),
```

---

## BUG 16 — `_load_json_list` in guardrails silently drops non-integer values (LOW)

**File:** `src/spark_intelligence/gateway/guardrails.py:326`

**Description:** The `_load_json_list` function loads a JSON list from runtime state and converts items to `int`. It filters with `isinstance(item, (int, float))` and then casts with `int(item)`. However, this means float values like `1.5` would be silently truncated to `1`, and any string values (which could occur from state corruption) are silently dropped. For event ID tracking (`is_duplicate_event`), this could cause duplicate detection to fail if event IDs are stored as floats or strings.

**Expected behavior:** Non-integer values should be handled more robustly, possibly with logging.

**Actual behavior:** Floats are truncated, strings are silently dropped.

**Fix:**
```python
def _load_json_list(*, state_db: StateDB, state_key: str) -> list[int]:
    with state_db.connect() as conn:
        row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return []
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    result: list[int] = []
    for item in payload:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result
```

---

## BUG 17 — Truncation in context capsule can cut in the middle of a multi-byte character (LOW)

**File:** `src/spark_intelligence/context/capsule.py:122`

**Description:** The `render` method truncates the context capsule with `rendered[: max_chars - 80].rstrip()`. If the text contains multi-byte UTF-8 characters (e.g., emojis, CJK characters), slicing by character count could land in the middle of a grapheme cluster or produce garbled text when the string contains combining characters. While Python 3 strings are Unicode code points (not bytes), slicing can still split surrogate pairs or combining character sequences.

**Expected behavior:** Truncation should respect character boundaries.

**Actual behavior:** Simple slice truncation may split combining characters.

**Fix:**
```python
# Replace line 122:
return rendered[: max_chars - 80].rstrip() + "\n[context capsule truncated]"

# With a safer truncation:
truncation_limit = max_chars - 80
if len(rendered) > truncation_limit:
    # Find a safe break point
    safe_end = rendered[:truncation_limit].rstrip()
    return safe_end + "\n[context capsule truncated]"
return rendered
```

---

## BUG 18 — `_validate_redirect_uri` only allows `http://` scheme but doesn't warn about HTTPS redirect URIs (LOW)

**File:** `src/spark_intelligence/gateway/oauth_callback.py:245-253`

**Description:** The `_validate_redirect_uri` function only allows `http://` redirect URIs, rejecting `https://`. This is technically correct for the local callback server (which only serves HTTP). However, the error message says "OAuth callback listener requires an http:// redirect URI" which doesn't explain that the user might need to change the provider's OAuth configuration to use a localhost HTTP redirect. Users who configure HTTPS redirect URIs (which is the default for most modern OAuth providers) get an opaque error.

**Expected behavior:** The error message should guide the user to configure a localhost HTTP redirect URI.

**Actual behavior:** The error is generic and doesn't help the user fix their configuration.

**Fix:**
```python
# Line 248:
if parsed.scheme != "http":
    raise ValueError(
        "OAuth callback listener requires an http:// redirect URI (e.g., http://127.0.0.1:1455/auth/callback). "
        "HTTPS redirect URIs are not supported for local callback capture. "
        "Update the provider's OAuth configuration to use a localhost HTTP redirect."
    )
```

---

## Summary

| # | Severity | Category | File | Description |
|---|----------|----------|------|-------------|
| 1 | HIGH | Security | auth/service.py | OAuth tokens stored in plaintext despite `_ciphertext` column names |
| 2 | HIGH | Logic | config/loader.py | WSL path translation triggered on non-WSL Linux systems |
| 3 | MEDIUM | Error handling | config/loader.py | Config mutation audit failures silently swallowed |
| 4 | MEDIUM | Logic | context/capsule.py | Diagnostic status returns wrong result for zero-failure None-finding case |
| 5 | MEDIUM | Security | security/redaction.py | `mask_secret` leaks partial key material for secrets >= 18 chars |
| 6 | MEDIUM | Security | gateway/whatsapp_webhook.py | Raw message text persisted in event log without redaction |
| 7 | HIGH | Provider routing | auth/providers.py | `codex_responses` api_mode has no guard against direct execution |
| 8 | MEDIUM | Memory | context/recent_conversation.py | Full JSONL file loaded into memory for tail read |
| 9 | LOW | Error handling | gateway/tracing.py | Malformed JSON lines crash the trace reader |
| 10 | MEDIUM | Logic | gateway/guardrails.py | MINUS SIGN (U+2212) replaced with spaced hyphen, breaking math |
| 11 | MEDIUM | Configuration | auth/service.py | Unauthenticated provider can become default provider |
| 12 | MEDIUM | Race condition | auth/oauth_state.py | OAuth state consumption not protected against concurrent access |
| 13 | MEDIUM | Input validation | execution/governed.py | "Governed" command execution has no input validation |
| 14 | LOW | Logic | context/harness.py | "Full access" maps to level 5, but valid levels are 1-4 |
| 15 | MEDIUM | State management | auth/service.py | OAuth login erases existing API key reference |
| 16 | LOW | Data integrity | gateway/guardrails.py | Float event IDs silently truncated, strings dropped |
| 17 | LOW | Text handling | context/capsule.py | Truncation can split multi-byte/combining characters |
| 18 | LOW | UX | gateway/oauth_callback.py | Opaque error for HTTPS redirect URI |
