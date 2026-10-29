"""Ładowanie konfiguracji (config.yaml + rules.yaml) z nadpisaniem przez zmienne środowiskowe."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Brak pliku konfiguracyjnego: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_dotenv(path: Path) -> None:
    """Minimalny parser .env - nie nadpisuje już ustawionych zmiennych."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    data: dict[str, Any]
    rules: dict[str, Any]
    root: Path = ROOT

    # --- sekcje ---
    @property
    def olx(self) -> dict[str, Any]:
        return self.data.get("olx", {})

    @property
    def searches(self) -> list[dict[str, Any]]:
        return self.data.get("searches", [])

    @property
    def filters(self) -> dict[str, Any]:
        return self.data.get("filters", {})

    @property
    def llm(self) -> dict[str, Any]:
        return self.data.get("llm", {})

    @property
    def ntfy(self) -> dict[str, Any]:
        n = dict(self.data.get("ntfy", {}))
        n["server"] = os.environ.get("NTFY_SERVER") or n.get("server") or "https://ntfy.sh"
        n["topic"] = os.environ.get("NTFY_TOPIC") or n.get("topic") or ""
        n["token"] = os.environ.get("NTFY_TOKEN") or n.get("token") or ""
        return n

    @property
    def state(self) -> dict[str, Any]:
        return self.data.get("state", {})

    @property
    def state_path(self) -> Path:
        p = Path(os.environ.get("OLX_STATE_PATH") or self.state.get("path") or ".olx_hunter_state.json")
        return p if p.is_absolute() else self.root / p

    @property
    def prompt_path(self) -> Path:
        return self.root / "prompts" / "analyst.md"

    @property
    def llm_enabled(self) -> bool:
        env = os.environ.get("OLX_LLM")
        if env is not None:
            return env.lower() in ("1", "true", "yes")
        return bool(self.llm.get("enabled", True))


def load_config(config_path: str | Path | None = None, rules_path: str | Path | None = None) -> Config:
    _load_dotenv(ROOT / ".env")
    cfg_path = Path(config_path) if config_path else ROOT / "config.yaml"
    rules_file = Path(rules_path) if rules_path else ROOT / "rules.yaml"
    return Config(data=_load_yaml(cfg_path), rules=_load_yaml(rules_file), root=cfg_path.resolve().parent)
