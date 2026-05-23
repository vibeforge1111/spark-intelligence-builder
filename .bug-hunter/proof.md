<!-- bug-hunter:link-proof -->
### 🔗 Link Proof

_Click through to see exactly what this PR changes, without rebuilding the hunt context._

- **Offending line (upstream)**: [src/spark_intelligence/gateway/routes.py:94](https://github.com/vibeforge1111/spark-intelligence-builder/blob/main/src/spark_intelligence/gateway/routes.py#L94)
- **Severity**: 🟢 LOW
- **Finding ID**: `SPARK-VG-003`
- **Category**: `error-message-actionability`
- **Detector**: `error-message-actionability` (spark-bug-hunter)
- **Discovered**: 2026-05-23

<!-- bug-hunter:link-proof -->
## Proof Kit — "Gateway route not found" is not actionable

### 🔴 Before

`src/spark_intelligence/gateway/routes.py:94`

```python
route = self.resolve(path=normalized_path, method=normalized_method)
if route is None:
    allowed_methods = self.allowed_methods_for_path(path=normalized_path)
    if allowed_methods:
        raise ValueError(
            f"Gateway route {normalized_path} rejects method '{normalized_method}'."
        )
    raise ValueError("Gateway route not found.")
```

The companion errors (method-rejection, content-type-rejection) already name the offending path / method / content-type. The "not found" branch alone was bare — a developer hitting this got `ValueError: Gateway route not found.` and had to instrument the registry to figure out which path was rejected and what's registered.

### 🟢 After

The bare branch now surfaces:
- the actual normalized path + method that was rejected
- a short list (up to 5) of registered paths sharing the same prefix, so a typo is obvious at a glance
- the recovery step: register via `GatewayRouteRegistry.register()`

Example improved message:

> Gateway route /api/v2/missions (method GET) is not registered. Registered paths starting with the same prefix: ['/api/v2/missions/board', '/api/v2/missions/status']. Verify the path spelling, or register the route via GatewayRouteRegistry.register().

### 🎯 Why this matters

Brings parity with the other two ValueErrors in `resolve_with_validation()` that already surface the rejected value. Closes the silent third branch where a developer was forced to debug via grep.

### 🔬 Evidence

| Field | Value |
|---|---|
| File | `src/spark_intelligence/gateway/routes.py` (+11 / -1 lines) |
| Category | `error-message-actionability` (developer-facing gateway path) |
| Severity | **LOW** UX, but high developer-debug friction |
| Detector | `vague-user-guidance` hunt — bare `not found.` in `raise ValueError` |
| Discovered | 2026-05-23 |

---
_Detected by spark-bug-hunter — vague-guidance detector._
