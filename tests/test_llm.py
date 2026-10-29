import json
from pathlib import Path
from types import SimpleNamespace

from olx_hunter.config import load_config
from olx_hunter.llm import LlmAnalyst, safe_analyze
from olx_hunter.models import Offer
from olx_hunter.scoring import Scorer

FIXTURE = Path(__file__).parent / "fixtures" / "offers.json"


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        payload = {
            "tytul_i_cena": "Lenovo M920q i5-8500T 16/512 za 450 zł",
            "specyfikacja": ["i5-8500T (6C/6T)", "16 GB DDR4", "512 GB NVMe"],
            "plusy": ["cena poniżej rynku", "faktura i gwarancja"],
            "minusy": ["sprawdź wentylator"],
            "werdykt_1_10": 8,
            "werdykt": "Kupować",
            "szacowana_cena_rynkowa_pln": 650,
            "ryzyko_scamu": "niskie",
            "podsumowanie": "Perełka poleasingowa.",
        }
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
        )


def _offer():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"][0]
    return Offer.from_api(data)


def test_llm_request_shape_and_parsing():
    cfg = load_config()
    fake = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    analyst = LlmAnalyst(cfg.prompt_path, model="claude-opus-5-5", effort="medium", client=client)
    offer = _offer()
    h = Scorer(cfg.rules, cfg.filters).score(offer)
    a = analyst.analyze(offer, h)

    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Łowcą okazji" in kw["system"][0]["text"] or "łowcą okazji" in kw["system"][0]["text"].lower()
    sent = kw["messages"][0]["content"]
    assert "M920q" in sent and "analiza_heurystyczna" in sent

    assert a.source == "llm" and a.verdict_score == 8 and a.verdict == "Kupować"
    assert a.fair_price_estimate == 650 and a.spec[0].startswith("i5-8500T")


def test_safe_analyze_falls_back_on_refusal():
    cfg = load_config()

    class Refusing:
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="x"), content=[])

    client = SimpleNamespace(beta=SimpleNamespace(messages=Refusing()))
    analyst = LlmAnalyst(cfg.prompt_path, client=client)
    offer = _offer()
    h = Scorer(cfg.rules, cfg.filters).score(offer)
    a = safe_analyze(analyst, offer, h)
    assert a.source == "heuristics" and 1 <= a.verdict_score <= 8
