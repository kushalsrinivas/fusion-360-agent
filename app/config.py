"""Central configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _data_dir() -> Path:
    base = os.environ.get("FUSION_AI_DATA_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".fusion-ai-agent"


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"

# Faust Machines fusion360-mcp-server (pypi: fusion360-mcp-server).
# Their pyproject pins only mcp>=1.0, but the mcp SDK broke its low-level
# server API after 1.16.x — pin the last known-good SDK.
FAUST_MCP_PIN = "mcp==1.16.0"
FAUST_COMMAND_SOCKET = f"uvx --with {FAUST_MCP_PIN} fusion360-mcp-server --mode socket"
FAUST_COMMAND_MOCK = f"uvx --with {FAUST_MCP_PIN} fusion360-mcp-server --mode mock"


@dataclass
class Config:
    # --- LLM ---
    # Gemini is the default provider: set GEMINI_API_KEY. Any OpenAI-compatible
    # endpoint works too via OPENAI_API_KEY + OPENAI_BASE_URL.
    llm_api_key: str = field(default_factory=lambda: (
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or
        os.environ.get("OPENAI_API_KEY") or ""
    ))
    llm_base_url: str = field(default_factory=lambda: os.environ.get(
        "OPENAI_BASE_URL",
        GEMINI_BASE_URL if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        else "https://api.openai.com/v1",
    ))
    llm_model: str = field(default_factory=lambda: os.environ.get(
        "FUSION_AI_MODEL",
        GEMINI_DEFAULT_MODEL if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        else "gpt-4o-mini",
    ))
    llm_temperature: float = field(
        default_factory=lambda: float(os.environ.get("FUSION_AI_TEMPERATURE", "0.2"))
    )

    # --- Fusion MCP bridge ---
    # "mock":       simulated Fusion document built into this app (default)
    # "faust":      faust-machines server in socket mode -> Fusion add-in :9876
    # "faust-mock": faust-machines server in its own mock mode (real MCP protocol,
    #               no Fusion needed) — great for protocol testing
    # "stdio"/"http": raw generic modes (command/URL from env)
    fusion_mode: str = field(default_factory=lambda: os.environ.get("FUSION_AI_MODE", "mock"))
    mcp_command: str = field(default_factory=lambda: os.environ.get("FUSION_MCP_COMMAND", ""))
    mcp_url: str = field(default_factory=lambda: os.environ.get("FUSION_MCP_URL", ""))

    # --- Safety limits ---
    max_plan_steps: int = field(default_factory=lambda: int(os.environ.get("FUSION_AI_MAX_PLAN_STEPS", "40")))
    max_repair_attempts: int = field(default_factory=lambda: int(os.environ.get("FUSION_AI_MAX_REPAIRS", "3")))
    approval_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("FUSION_AI_APPROVAL_TIMEOUT", "600"))
    )
    simulate_failures: bool = field(
        default_factory=lambda: os.environ.get("FUSION_AI_SIMULATE_FAILURES", "0") == "1"
    )

    # --- Storage ---
    data_dir: Path = field(default_factory=_data_dir)

    @property
    def llm_available(self) -> bool:
        return bool(self.llm_api_key)


def get_config(**overrides) -> Config:
    cfg = Config()
    for key, value in overrides.items():
        if value is not None:
            setattr(cfg, key, value)
    return cfg
