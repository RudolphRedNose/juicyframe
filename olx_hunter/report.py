"""Formatowanie raportu okazji w strukturze wymaganej przez użytkownika."""

from __future__ import annotations

from .models import Analysis, Heuristics, Offer


def _bullets(items: list[str]) -> str:
    return "\n".join(f"  - {i}" for i in items) if items else "  - (brak)"


def format_report(offer: Offer, analysis: Analysis, heuristics: Heuristics | None = None) -> str:
    fair = f" (szac. cena rynkowa: ~{analysis.fair_price_estimate:.0f} zł)" if analysis.fair_price_estimate else ""
    lines = [
        f"**Tytuł i Cena:** {analysis.title_and_price}{fair}",
        f"**Link do oferty:** {offer.url}",
        "**Specyfikacja techniczna:**",
        _bullets(analysis.spec),
        "**Dlaczego warto (Plusy):**",
        _bullets(analysis.pros),
        "**Pułapki / Na co uważać (Minusy/Ryzyko):**",
        _bullets(analysis.cons),
        f"**Werdykt:** {analysis.verdict} ({analysis.verdict_score}/10), ryzyko scamu: {analysis.scam_risk}",
    ]
    if analysis.summary:
        lines.append(f"_{analysis.summary}_")
    if heuristics is not None:
        src = "LLM" if analysis.source == "llm" else "heurystyka"
        lines.append(f"_Heurystyka: {heuristics.score}/100, źródło werdyktu: {src}_")
    return "\n".join(lines)


def format_push_message(offer: Offer, analysis: Analysis, extra_note: str = "") -> str:
    """Krótsza wersja na telefon: ntfy obsługuje markdown, ale ekran jest mały."""
    loc = f"{offer.city}" + (f", {offer.region}" if offer.region else "")
    parts = []
    if extra_note:
        parts.append(f"**{extra_note}**")
    if analysis.summary:
        parts.append(analysis.summary)
    parts.append("**Spec:** " + "; ".join(analysis.spec[:5]))
    parts.append("**Plusy:** " + "; ".join(analysis.pros[:4]))
    parts.append("**Minusy:** " + "; ".join(analysis.cons[:4]))
    meta = [loc or "brak lokalizacji"]
    meta.append("Przesyłka OLX" if offer.safedeal else "bez Przesyłki OLX")
    if offer.negotiable:
        meta.append("do negocjacji")
    if analysis.fair_price_estimate:
        meta.append(f"rynek ~{analysis.fair_price_estimate:.0f} zł")
    parts.append("_" + " | ".join(meta) + "_")
    parts.append(offer.url)
    return "\n\n".join(parts)


def format_rejection(offer: Offer, h: Heuristics) -> str:
    price = f"{offer.price:.0f} zł" if offer.price else "brak ceny"
    reason = h.reject_reason if h.rejected else f"wynik {h.score}/100"
    return f"- [{price}] {offer.title[:70]} -> {reason}"
