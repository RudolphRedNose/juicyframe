"""Deterministyczna heurystyka: profil referencyjny, stosunek ceny, flagi ryzyka, ukryte koszty.

Wynik 0-100. Celem nie jest ostateczny werdykt (to robi LLM), tylko tani filtr wstępny,
który odrzuca śmieci i scamy, zanim zapłacimy za analizę.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .models import Heuristics, Offer

_FLAGS = re.IGNORECASE | re.DOTALL


def _compile(rules: list[dict[str, Any]]) -> list[tuple[re.Pattern[str], dict[str, Any]]]:
    return [(re.compile(r["pattern"], _FLAGS), r) for r in rules]


class Scorer:
    def __init__(self, rules: dict[str, Any], filters: Optional[dict[str, Any]] = None):
        self.references = _compile(rules.get("reference_prices", []))
        self.obsolete = [re.compile(p, _FLAGS) for p in rules.get("obsolete_patterns", [])]
        self.hidden_costs = _compile(rules.get("hidden_costs", []))
        self.scam_phrases = _compile(rules.get("scam_phrases", []))
        self.positives = _compile(rules.get("positive_signals", []))
        filters = filters or {}
        self.symbolic_price = float(filters.get("symbolic_price", 150))
        self.exclude_keywords = [k.lower() for k in filters.get("exclude_keywords", [])]
        self.exclude_business = bool(filters.get("exclude_business", False))

    # --- publiczne ---
    def score(self, offer: Offer, search: Optional[dict[str, Any]] = None) -> Heuristics:
        text = offer.text
        text_lc = offer.text_lc
        title_lc = offer.title.lower()
        h = Heuristics(score=50)

        # 1. Twarde wykluczenia
        for kw in self.exclude_keywords:
            if kw in text_lc:
                return self._reject(h, f"wykluczone słowo: '{kw}'")
        if self.exclude_business and offer.is_business:
            return self._reject(h, "sprzedawca biznesowy (wykluczony w konfiguracji)")
        if offer.condition == "damaged":
            return self._reject(h, "stan: uszkodzony")
        if search:
            must_any = [m.lower() for m in search.get("must_match_any", [])]
            if must_any and not any(m in title_lc for m in must_any):
                return self._reject(h, "tytuł nie pasuje do żadnego z wymaganych słów wyszukiwania")
            for kw in search.get("exclude_keywords", []):
                if kw.lower() in text_lc:
                    return self._reject(h, f"wykluczone słowo wyszukiwania: '{kw}'")

        # 2. Przestarzała architektura (chyba że cena symboliczna)
        for pat in self.obsolete:
            m = pat.search(text)
            if m:
                if offer.price is not None and offer.price <= self.symbolic_price:
                    h.notes.append(f"Przestarzała architektura ('{m.group(0)}'), ale cena symboliczna.")
                    h.score -= 10
                    break
                return self._reject(h, f"przestarzała / wykastrowana architektura: '{m.group(0)}'")

        # 3. Profil referencyjny i stosunek ceny
        ref = self._find_reference(text)
        if ref:
            h.reference_label = ref["label"]
            h.reference_fair = float(ref["fair"])
            h.reference_good = float(ref["good"])
            if offer.price:
                ratio = offer.price / h.reference_fair
                h.price_ratio = ratio
                scam_line = float(ref.get("scam", h.reference_good * 0.45))
                if offer.price < scam_line:
                    h.red_flags.append(
                        f"Cena {offer.price:.0f} zł jest podejrzanie niska vs rynek ~{h.reference_fair:.0f} zł "
                        f"(poniżej progu scamu {scam_line:.0f} zł)."
                    )
                    h.score -= 25
                elif offer.price <= h.reference_good:
                    h.positives.append(f"Cena poniżej progu okazji ({h.reference_good:.0f} zł) dla: {ref['label']}.")
                    h.score += 30
                elif ratio <= 1.0:
                    h.positives.append(f"Cena w normie rynkowej ({h.reference_fair:.0f} zł) dla: {ref['label']}.")
                    h.score += 10
                elif ratio <= 1.2:
                    h.notes.append(f"Cena ~{(ratio - 1) * 100:.0f}% powyżej rynku ({h.reference_fair:.0f} zł) - do negocjacji.")
                    h.score -= 5
                else:
                    h.notes.append(f"Cena {(ratio - 1) * 100:.0f}% powyżej rynku ({h.reference_fair:.0f} zł).")
                    h.score -= 25
            else:
                h.notes.append("Brak ceny w ofercie (do uzgodnienia) - trudno ocenić.")
                h.score -= 10
        else:
            h.notes.append("Brak profilu referencyjnego - ocena ceny tylko przez LLM.")
            h.score -= 5

        # 4. Ukryte koszty
        for pat, rule in self.hidden_costs:
            if pat.search(text):
                h.hidden_costs.append(rule["note"])
                h.score -= int(rule.get("penalty", 0))

        # 5. Frazy scamowe
        for pat, rule in self.scam_phrases:
            if pat.search(text):
                h.red_flags.append(rule["note"])
                h.score -= int(rule.get("penalty", 0))

        # 6. Sygnały pozytywne
        for pat, rule in self.positives:
            if pat.search(text):
                h.positives.append(rule["note"])
                h.score += int(rule.get("bonus", 0))

        # 7. Profil sprzedawcy i oferty
        age = offer.seller_age_days
        if age is not None:
            if age < 1:
                h.red_flags.append("Konto sprzedawcy utworzone w dniu wystawienia ogłoszenia.")
                h.score -= 20
            elif age < 7:
                h.red_flags.append(f"Bardzo młode konto sprzedawcy ({age:.0f} dni).")
                h.score -= 10
            elif age > 365:
                h.positives.append(f"Konto sprzedawcy ma {age / 365:.1f} lat.")
                h.score += 3
        if offer.photo_count == 0:
            h.red_flags.append("Brak zdjęć.")
            h.score -= 15
        elif offer.photo_count == 1:
            h.notes.append("Tylko jedno zdjęcie - sprawdź, czy nie stockowe.")
            h.score -= 5
        if len(offer.description) < 80:
            h.notes.append("Bardzo krótki opis - dopytaj o szczegóły.")
            h.score -= 5
        if offer.safedeal:
            h.positives.append("Dostępna Przesyłka OLX (bezpieczna transakcja).")
            h.score += 8
        else:
            h.notes.append("Brak Przesyłki OLX - preferuj odbiór osobisty.")
            h.score -= 3
        if offer.negotiable:
            h.positives.append("Cena do negocjacji.")
            h.score += 2
        if offer.is_business:
            h.notes.append("Sprzedawca biznesowy (komis/sklep) - zwykle faktura, ale wyższa cena.")

        h.score = max(0, min(100, h.score))
        return h

    # --- pomocnicze ---
    def _find_reference(self, text: str) -> Optional[dict[str, Any]]:
        for pat, rule in self.references:
            if pat.search(text):
                return rule
        return None

    @staticmethod
    def _reject(h: Heuristics, reason: str) -> Heuristics:
        h.rejected = True
        h.reject_reason = reason
        h.score = 0
        return h


def fallback_analysis(offer: Offer, h: Heuristics):
    """Gdy LLM jest wyłączony lub niedostępny - raport zbudowany z samej heurystyki."""
    from .models import Analysis

    # Bez weryfikacji LLM nie dajemy więcej niż 8/10.
    verdict_score = max(1, min(8, round(h.score / 10)))
    spec = [f"{k}: {v}" for k, v in list(offer.params.items())[:8]] or ["(brak parametrów w ofercie)"]
    price = f"{offer.price:.0f} {offer.currency}" if offer.price else "cena do uzgodnienia"
    return Analysis(
        title_and_price=f"{offer.title} - {price}",
        spec=spec,
        pros=h.positives or ["(brak wykrytych plusów)"],
        cons=(h.red_flags + h.hidden_costs + h.notes) or ["(brak wykrytych minusów)"],
        verdict_score=verdict_score,
        verdict=Analysis.verdict_for(verdict_score),
        fair_price_estimate=h.reference_fair,
        scam_risk="wysokie" if any("scam" in f.lower() or "podejrzanie" in f.lower() for f in h.red_flags)
        else ("średnie" if h.red_flags else "niskie"),
        summary=f"Ocena heurystyczna {h.score}/100" + (f", profil: {h.reference_label}" if h.reference_label else ""),
        source="heuristics",
    )
