import json
from pathlib import Path

import pytest

from olx_hunter.config import load_config
from olx_hunter.models import Offer
from olx_hunter.report import format_report
from olx_hunter.scoring import Scorer, fallback_analysis

FIXTURE = Path(__file__).parent / "fixtures" / "offers.json"


@pytest.fixture(scope="module")
def offers():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
    return {o["id"]: Offer.from_api(o) for o in data}


@pytest.fixture(scope="module")
def scorer():
    cfg = load_config()
    return Scorer(cfg.rules, cfg.filters)


def test_parse_offer(offers):
    o = offers[900000001]
    assert o.price == 450 and o.currency == "PLN" and o.negotiable
    assert o.condition == "used"
    assert o.city == "Warszawa"
    assert o.safedeal and o.is_business
    assert o.params["Pamięć RAM"] == "16 GB"
    assert o.seller_age_days > 365


def test_good_tiny_scores_high(offers, scorer):
    h = scorer.score(offers[900000001])
    assert not h.rejected
    assert h.reference_label.startswith("Lenovo M720q/M920q")
    assert h.score >= 75
    assert any("okazji" in p for p in h.positives)
    assert not h.red_flags


def test_scam_gpu_flagged(offers, scorer):
    h = scorer.score(offers[900000002])
    assert not h.rejected
    assert h.score < 30
    joined = " ".join(h.red_flags).lower()
    assert "podejrzanie niska" in joined
    assert "przedpłat" in joined
    assert "konto sprzedawcy utworzone w dniu" in joined


def test_old_xeon_rejected(offers, scorer):
    h = scorer.score(offers[900000003])
    assert h.rejected
    assert "xeon" in h.reject_reason.lower()


def test_hidden_costs_penalised(offers, scorer):
    h = scorer.score(offers[900000004])
    assert not h.rejected
    notes = " ".join(h.hidden_costs).lower()
    assert "zasilacz" in notes and "dysk" in notes and "gwarancji" in notes
    assert h.score < 55


def test_symbolic_price_passes_obsolete(offers, scorer):
    h = scorer.score(offers[900000005])
    assert not h.rejected
    assert any("symboliczna" in n for n in h.notes)


def test_must_match_any_filter(offers, scorer):
    h = scorer.score(offers[900000001], {"must_match_any": ["optiplex"]})
    assert h.rejected


def test_fallback_report_format(offers, scorer):
    o = offers[900000001]
    h = scorer.score(o)
    a = fallback_analysis(o, h)
    text = format_report(o, a, h)
    for header in ("**Tytuł i Cena:**", "**Link do oferty:**", "**Specyfikacja techniczna:**",
                   "**Dlaczego warto (Plusy):**", "**Pułapki / Na co uważać (Minusy/Ryzyko):**", "**Werdykt:**"):
        assert header in text
    assert a.verdict in ("Kupować", "Negocjować", "Odpuścić")
