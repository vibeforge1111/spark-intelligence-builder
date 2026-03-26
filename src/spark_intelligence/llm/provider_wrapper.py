from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from spark_intelligence.llm.direct_provider import DirectProviderRequest, execute_direct_provider_prompt


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 3:
        print(
            "Usage: python -m spark_intelligence.llm.provider_wrapper <system_prompt_path> <user_prompt_path> <response_path>",
            file=sys.stderr,
        )
        return 2

    system_prompt = Path(args[0]).read_text(encoding="utf-8")
    user_prompt = Path(args[1]).read_text(encoding="utf-8")
    response_path = Path(args[2])

    provider = DirectProviderRequest(
        provider_id=_required_env("SPARK_INTELLIGENCE_PROVIDER_ID"),
        provider_kind=_required_env("SPARK_INTELLIGENCE_PROVIDER_KIND"),
        auth_method=_required_env("SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD"),
        api_mode=_required_env("SPARK_INTELLIGENCE_PROVIDER_API_MODE"),
        base_url=_required_env("SPARK_INTELLIGENCE_PROVIDER_BASE_URL"),
        model=_required_env("SPARK_INTELLIGENCE_PROVIDER_MODEL"),
        secret_value=_required_env("SPARK_INTELLIGENCE_PROVIDER_SECRET"),
    )
    payload = execute_direct_provider_prompt(
        provider=provider,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    response_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required provider wrapper env var: {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
