"""Runtime config loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_api_base: str
    console_host: str
    console_port: int
    agents_dir: Path
    frontend_dir: Path
    mcp_config_path: Path


def load() -> Settings:
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_api_base=os.environ.get(
            "ANTHROPIC_API_BASE", "https://api.anthropic.com"
        ),
        console_host=os.environ.get("CONSOLE_HOST", "127.0.0.1"),
        console_port=int(os.environ.get("CONSOLE_PORT", "8787")),
        agents_dir=ROOT / "agents",
        frontend_dir=ROOT / "frontend",
        mcp_config_path=ROOT / "mcp" / "servers.json",
    )


settings = load()
