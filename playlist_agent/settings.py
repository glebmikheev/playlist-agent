import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str = "gemini-3.1-flash-lite"
    thinking_budget: int = 1024
    thinking_level: str = "low"
    max_output_tokens: int = 4096
    max_input_tokens: int = 24000
    timeout_s: int = 180

    @classmethod
    def load(cls):
        values = {**dotenv_values(ROOT / ".env"), **os.environ}
        return cls(
            api_key=values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY") or "",
            model=values.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            thinking_budget=int(values.get("GEMINI_THINKING_BUDGET", "1024")),
            thinking_level=values.get("GEMINI_THINKING_LEVEL", "low"),
            max_output_tokens=int(values.get("GEMINI_MAX_OUTPUT_TOKENS", "4096")),
            max_input_tokens=int(values.get("GEMINI_MAX_INPUT_TOKENS", "24000")),
            timeout_s=int(values.get("AGENT_TIMEOUT_SECONDS", "180")),
        )
