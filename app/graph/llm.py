"""Thin OpenAI-compatible LLM client with JSON-schema structured output.

Every agent node uses this with a strict schema. If no API key is configured
the system runs in deterministic offline mode (rule-based heuristics), so the
whole pipeline is testable without network access.
"""
from __future__ import annotations

import json
from typing import Any

from app.config import Config


class OfflineModeError(Exception):
    """Raised when the LLM is requested but no API key is configured."""


_RUNTIME_CONFIG = None


def _runtime_config():
    global _RUNTIME_CONFIG
    if _RUNTIME_CONFIG is None:
        from app.config import get_config
        _RUNTIME_CONFIG = get_config()
    return _RUNTIME_CONFIG


def set_runtime_config(config: Config) -> None:
    """Called once at startup so nodes share the CLI-provided config."""
    global _RUNTIME_CONFIG
    _RUNTIME_CONFIG = config


class LLMClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    @classmethod
    def from_state(cls, state) -> "LLMClient":
        """Rebuild a client from runtime config carried on the graph state."""
        cfg = getattr(state.get("bridge"), "config", None) if isinstance(state, dict) else None
        return cls(cfg or _runtime_config())

    @property
    def available(self) -> bool:
        return self.config.llm_available

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        if not self.available:
            raise OfflineModeError("No OPENAI_API_KEY configured; using offline heuristics")

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.config.llm_api_key, base_url=self.config.llm_base_url)
        response = await client.chat.completions.create(
            model=self.config.llm_model,
            temperature=self.config.llm_temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "cad_agent_output", "schema": schema, "strict": False},
            },
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(f"LLM returned non-object JSON: {type(data)}")
        return data
