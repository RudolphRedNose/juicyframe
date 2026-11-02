"""Modele danych: oferta z OLX, wynik heurystyki, wynik analizy LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Offer:
    id: int
    title: str
    url: str
    price: Optional[float]
    currency: str = "PLN"
    negotiable: bool = False
    description: str = ""
    condition: str = ""              # "used" / "new" / "damaged" / ""
    city: str = ""
    region: str = ""
    created_at: Optional[datetime] = None
    refreshed_at: Optional[datetime] = None
    seller_name: str = ""
    seller_since: Optional[datetime] = None
    is_business: bool = False
    photo_count: int = 0
    safedeal: bool = False           # Przesyłka OLX / bezpieczna transakcja
    delivery_active: bool = False
    params: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Tytuł + opis + parametry, do dopasowywania regexów."""
        params = " ".join(f"{k}: {v}" for k, v in self.params.items())
        return f"{self.title}\n{self.description}\n{params}"

    @property
    def text_lc(self) -> str:
        return self.text.lower()

    @property
    def age_hours(self) -> Optional[float]:
        ref = self.refreshed_at or self.created_at
        if not ref:
            return None
        return (datetime.now(timezone.utc) - ref).total_seconds() / 3600

    @property
    def seller_age_days(self) -> Optional[float]:
        if not self.seller_since:
            return None
        # Wiek konta w chwili wystawienia oferty (konto założone tuż przed ogłoszeniem = sygnał scamu).
        ref = self.created_at or datetime.now(timezone.utc)
        return max(0.0, (ref - self.seller_since).total_seconds() / 86400)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Offer":
        """Buduje Offer z obiektu zwracanego przez api/v1/offers. Parsowanie defensywne."""
        params: dict[str, str] = {}
        price: Optional[float] = None
        currency = "PLN"
        negotiable = False
        condition = ""
        for p in data.get("params") or []:
            key = p.get("key") or ""
            val = p.get("value") or {}
            if key == "price":
                raw_price = val.get("value")
                try:
                    price = float(raw_price) if raw_price is not None else None
                except (TypeError, ValueError):
                    price = None
                currency = val.get("currency") or currency
                negotiable = bool(val.get("negotiable"))
                continue
            if key == "state":
                condition = str(val.get("key") or "")
            label = val.get("label") or val.get("key") or str(val.get("value", ""))
            if p.get("name") and label:
                params[str(p["name"])] = str(label)

        user = data.get("user") or {}
        location = data.get("location") or {}
        delivery = (data.get("delivery") or {}).get("rock") or {}
        safedeal = data.get("safedeal") or {}

        return cls(
            id=int(data.get("id") or 0),
            title=str(data.get("title") or "").strip(),
            url=str(data.get("url") or ""),
            price=price,
            currency=currency,
            negotiable=negotiable,
            description=re.sub(r"<[^>]+>", " ", str(data.get("description") or "")).strip(),
            condition=condition,
            city=str((location.get("city") or {}).get("name") or ""),
            region=str((location.get("region") or {}).get("name") or ""),
            created_at=_parse_dt(data.get("created_time")),
            refreshed_at=_parse_dt(data.get("last_refresh_time")),
            seller_name=str(user.get("name") or ""),
            seller_since=_parse_dt(user.get("created")),
            is_business=bool(data.get("business") or user.get("company_name")),
            photo_count=len(data.get("photos") or []),
            safedeal=bool(safedeal.get("allowed_quantity") or safedeal.get("weight") or delivery.get("active")),
            delivery_active=bool(delivery.get("active")),
            params=params,
            raw=data,
        )

    def to_llm_dict(self) -> dict[str, Any]:
        """Kompaktowa reprezentacja dla LLM (bez surowego JSON-a)."""
        return {
            "id": self.id,
            "tytul": self.title,
            "url": self.url,
            "cena": self.price,
            "waluta": self.currency,
            "do_negocjacji": self.negotiable,
            "stan": self.condition,
            "lokalizacja": f"{self.city}, {self.region}".strip(", "),
            "wystawiono": self.created_at.isoformat() if self.created_at else None,
            "odswiezono": self.refreshed_at.isoformat() if self.refreshed_at else None,
            "sprzedawca": self.seller_name,
            "konto_od": self.seller_since.isoformat() if self.seller_since else None,
            "konto_wiek_dni": round(self.seller_age_days, 1) if self.seller_age_days is not None else None,
            "firma": self.is_business,
            "liczba_zdjec": self.photo_count,
            "przesylka_olx": self.safedeal,
            "parametry": self.params,
            "opis": self.description[:4000],
        }


@dataclass
class Heuristics:
    """Wynik szybkiej, deterministycznej oceny (bez LLM)."""
    score: int                       # 0-100
    rejected: bool = False
    reject_reason: str = ""
    reference_label: str = ""
    reference_fair: Optional[float] = None
    reference_good: Optional[float] = None
    price_ratio: Optional[float] = None   # cena / fair
    red_flags: list[str] = field(default_factory=list)
    hidden_costs: list[str] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wynik_0_100": self.score,
            "profil_referencyjny": self.reference_label or None,
            "cena_rynkowa_uzywany": self.reference_fair,
            "cena_okazyjna": self.reference_good,
            "stosunek_ceny_do_rynkowej": round(self.price_ratio, 2) if self.price_ratio else None,
            "czerwone_flagi": self.red_flags,
            "ukryte_koszty": self.hidden_costs,
            "plusy": self.positives,
            "uwagi": self.notes,
        }


@dataclass
class Analysis:
    """Wynik analizy LLM w formacie raportu okazji."""
    title_and_price: str
    spec: list[str]
    pros: list[str]
    cons: list[str]
    verdict_score: int               # 1-10
    verdict: str                     # Kupować / Negocjować / Odpuścić
    fair_price_estimate: Optional[float] = None
    scam_risk: str = "niskie"        # niskie / średnie / wysokie
    summary: str = ""
    source: str = "llm"              # "llm" lub "heuristics"

    @staticmethod
    def verdict_for(score: int) -> str:
        if score >= 7:
            return "Kupować"
        if score >= 4:
            return "Negocjować"
        return "Odpuścić"
