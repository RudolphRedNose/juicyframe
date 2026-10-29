"""Analiza oferty przez Claude (Anthropic SDK) ze strukturalnym wynikiem JSON.

Domyślny model: claude-opus-5-5. Thinking jest adaptacyjny (domyślnie włączony na tym modelu),
głębokość sterowana przez output_config.effort. Włączony jest server-side fallback ("default"):
jeśli klasyfikator bezpieczeństwa odrzuci żądanie, API samo ponowi je na modelu zapasowym.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import anthropic

from .models import Analysis, Heuristics, Offer

log = logging.getLogger(__name__)

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tytul_i_cena": {"type": "string", "description": "Co i za ile, jedno zdanie."},
        "specyfikacja": {"type": "array", "items": {"type": "string"}, "description": "Kluczowe parametry w punktach."},
        "plusy": {"type": "array", "items": {"type": "string"}},
        "minusy": {"type": "array", "items": {"type": "string"}, "description": "Pułapki, ryzyka, ukryte koszty."},
        "werdykt_1_10": {"type": "integer", "minimum": 1, "maximum": 10},
        "werdykt": {"type": "string", "enum": ["Kupować", "Negocjować", "Odpuścić"]},
        "szacowana_cena_rynkowa_pln": {"type": ["number", "null"],
                                       "description": "Twoja ocena uczciwej ceny używanego egzemplarza w PLN."},
        "ryzyko_scamu": {"type": "string", "enum": ["niskie", "średnie", "wysokie"]},
        "podsumowanie": {"type": "string", "description": "1-2 zdania dla powiadomienia push."},
    },
    "required": ["tytul_i_cena", "specyfikacja", "plusy", "minusy", "werdykt_1_10", "werdykt",
                 "szacowana_cena_rynkowa_pln", "ryzyko_scamu", "podsumowanie"],
    "additionalProperties": False,
}


class LlmAnalyst:
    def __init__(self, prompt_path: Path, model: str = "claude-opus-5-5", effort: str = "medium",
                 client: Optional[anthropic.Anthropic] = None):
        self.system_prompt = prompt_path.read_text(encoding="utf-8")
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def analyze(self, offer: Offer, heuristics: Heuristics) -> Analysis:
        user_payload = {
            "oferta": offer.to_llm_dict(),
            "analiza_heurystyczna": heuristics.to_dict(),
        }
        user_text = (
            "Przeanalizuj poniższą ofertę z OLX i zwróć raport okazji w zadanym formacie JSON.\n\n"
            + json.dumps(user_payload, ensure_ascii=False, indent=1)
        )
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
            },
            # Stały prompt systemowy - cache'owany; zmienna oferta trafia do messages.
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_text}],
        )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"Model odmówił analizy ({getattr(details, 'category', None)}).")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Odpowiedź modelu ucięta (max_tokens).")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise RuntimeError("Model nie zwrócił bloku tekstowego.")
        data = json.loads(text)
        score = int(data["werdykt_1_10"])
        return Analysis(
            title_and_price=data["tytul_i_cena"],
            spec=list(data["specyfikacja"]),
            pros=list(data["plusy"]),
            cons=list(data["minusy"]),
            verdict_score=score,
            verdict=data.get("werdykt") or Analysis.verdict_for(score),
            fair_price_estimate=data.get("szacowana_cena_rynkowa_pln"),
            scam_risk=data.get("ryzyko_scamu", "niskie"),
            summary=data.get("podsumowanie", ""),
            source="llm",
        )


def safe_analyze(analyst: Optional[LlmAnalyst], offer: Offer, heuristics: Heuristics) -> Analysis:
    """Analiza LLM z awaryjnym przejściem na heurystykę, gdy API nie odpowiada."""
    from .scoring import fallback_analysis

    if analyst is None:
        return fallback_analysis(offer, heuristics)
    try:
        return analyst.analyze(offer, heuristics)
    except anthropic.AuthenticationError:
        log.error("Nieprawidłowy ANTHROPIC_API_KEY - przechodzę na heurystykę.")
    except anthropic.RateLimitError as exc:
        log.warning("Rate limit API (%s) - heurystyka dla oferty %s.", exc, offer.id)
    except anthropic.APIStatusError as exc:
        log.warning("Błąd API %s: %s - heurystyka dla oferty %s.", exc.status_code, exc.message, offer.id)
    except anthropic.APIConnectionError as exc:
        log.warning("Brak połączenia z API (%s) - heurystyka dla oferty %s.", exc, offer.id)
    except (RuntimeError, ValueError, KeyError) as exc:
        log.warning("Analiza LLM nieudana (%s) - heurystyka dla oferty %s.", exc, offer.id)
    return fallback_analysis(offer, heuristics)
