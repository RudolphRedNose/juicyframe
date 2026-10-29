"""Pamięć skanera: które oferty już widzieliśmy i po jakiej cenie (żeby nie spamować)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"seen": {}, "last_run": None}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.data = {"seen": {}, "last_run": None}
        self.data.setdefault("seen", {})

    def get(self, offer_id: int) -> Optional[dict[str, Any]]:
        return self.data["seen"].get(str(offer_id))

    def is_seen(self, offer_id: int) -> bool:
        return str(offer_id) in self.data["seen"]

    def price_drop_pct(self, offer_id: int, price: Optional[float]) -> Optional[float]:
        """Zwraca procent spadku ceny vs ostatnio zapisana (None, gdy brak danych lub cena nie spadła)."""
        prev = self.get(offer_id)
        if not prev or price is None or not prev.get("price"):
            return None
        old = float(prev["price"])
        if price >= old:
            return None
        return (old - price) / old * 100

    def mark(self, offer_id: int, price: Optional[float], score: int, notified: bool, verdict: Optional[int] = None) -> None:
        prev = self.get(offer_id) or {}
        self.data["seen"][str(offer_id)] = {
            "price": price,
            "score": score,
            "verdict": verdict if verdict is not None else prev.get("verdict"),
            "notified": notified or bool(prev.get("notified")),
            "first_seen": prev.get("first_seen") or datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def prune(self, max_entries: int = 5000) -> None:
        seen = self.data["seen"]
        if len(seen) <= max_entries:
            return
        ordered = sorted(seen.items(), key=lambda kv: kv[1].get("last_seen", ""))
        for key, _ in ordered[: len(seen) - max_entries]:
            del seen[key]

    def save(self) -> None:
        self.data["last_run"] = datetime.now(timezone.utc).isoformat()
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
