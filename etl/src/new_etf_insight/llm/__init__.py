from __future__ import annotations

import os
from pathlib import Path

from new_etf_insight.llm.base import LlmProvider
from new_etf_insight.llm.codex_provider import CodexProvider
from new_etf_insight.llm.openclaw_provider import OpenClawProvider


DEFAULT_PROVIDER = "codex"
PROVIDER_ENV = "ETF_LLM_PROVIDER"


def get_provider(provider_name: str | None = None) -> LlmProvider:
    name = (provider_name or os.getenv(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()

    if name in {"codex", "codex_cli"}:
        return CodexProvider()
    if name == "openclaw":
        return OpenClawProvider()

    raise ValueError(f"Unsupported LLM provider: {name}")


def generate_json(
    prompt: str,
    *,
    output_schema_path: Path,
    search: bool = False,
    provider_name: str | None = None,
) -> str:
    provider = get_provider(provider_name)
    return provider.generate_json(
        prompt,
        output_schema_path=output_schema_path,
        search=search,
    )
