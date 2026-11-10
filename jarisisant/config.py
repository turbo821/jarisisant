"""Настройки: ключи и модели читаются из файла .env в корне проекта."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    kind: str  # "openai" (OpenAI-совместимый API) или "anthropic"
    model: str
    base_url: str = ""
    api_key_env: str = ""  # пусто = ключ не нужен (Ollama)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def profiles() -> dict[str, Profile]:
    items = [
        Profile(
            key="ollama",
            label="Локальная (Ollama)",
            kind="openai",
            model=_env("OLLAMA_MODEL", "qwen3:8b"),
            base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        ),
        Profile(
            key="gemini",
            label="Gemini (бесплатная)",
            kind="openai",
            model=_env("GEMINI_MODEL", "gemini-flash-latest"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key_env="GEMINI_API_KEY",
        ),
        Profile(
            key="deepseek",
            label="DeepSeek",
            kind="openai",
            model=_env("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
        ),
        Profile(
            key="claude",
            label="Claude (платная)",
            kind="anthropic",
            model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
            api_key_env="ANTHROPIC_API_KEY",
        ),
    ]
    return {p.key: p for p in items}


def default_profile() -> str:
    key = _env("DEFAULT_PROFILE", "ollama").lower()
    return key if key in profiles() else "ollama"


def claude_effort() -> str:
    effort = _env("CLAUDE_EFFORT", "medium").lower()
    return effort if effort in ("low", "medium", "high", "xhigh", "max") else "medium"


def extra_allowed_dirs() -> list[Path]:
    raw = os.environ.get("ALLOWED_DIRS", "")
    return [Path(p.strip()) for p in raw.split(";") if p.strip()]
