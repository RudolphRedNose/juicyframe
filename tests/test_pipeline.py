import json
from pathlib import Path

from olx_hunter import cli
from olx_hunter.config import load_config
from olx_hunter.models import Analysis, Offer
from olx_hunter.notify import NtfyNotifier
from olx_hunter.state import State

FIXTURE = Path(__file__).parent / "fixtures" / "offers.json"


class FakeResponse:
    status_code = 200

    def json(self):
        return {"id": "abc"}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return FakeResponse()


def test_ntfy_publish_json_body():
    s = FakeSession()
    n = NtfyNotifier("https://ntfy.example", "topic1", token="tk", session=s)
    o = Offer(id=1, title="Lenovo M920q", url="https://olx.pl/x", price=450)
    a = Analysis("Lenovo M920q - 450 zł", ["i5-8500T"], ["tanio"], ["brak dysku"], 8, "Kupować", 650, "niskie", "Perełka.")
    n.notify_deal(o, a, extra_note="Spadek ceny o 12%")
    url, body, headers = s.calls[0]
    assert url == "https://ntfy.example"
    assert body["topic"] == "topic1" and body["priority"] == 5 and body["click"] == o.url
    assert body["title"].startswith("[8/10 Kupować]")
    assert "Spadek ceny" in body["message"] and body["markdown"] is True
    assert headers["Authorization"] == "Bearer tk"


def test_scan_fixture_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OLX_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    cfg = load_config()
    stats = cli.run_scan(cfg, use_llm=False, dry_run=True, fixture=FIXTURE, verbose=True)
    out = capsys.readouterr().out
    assert stats["fetched"] == 5
    assert stats["rejected"] == 1          # Xeon
    assert stats["notified"] >= 1          # M920q
    assert "M920q" in out and "**Werdykt:**" in out

    # drugi przebieg: wszystko już widziane, nic nowego
    stats2 = cli.run_scan(cfg, use_llm=False, dry_run=True, fixture=FIXTURE)
    assert stats2["seen"] == 5 and stats2["notified"] == 0

    # spadek ceny o 20% -> ponowne powiadomienie
    state = State(tmp_path / "state.json")
    state.data["seen"]["900000001"]["price"] = 600
    state.save()
    stats3 = cli.run_scan(cfg, use_llm=False, dry_run=True, fixture=FIXTURE)
    assert stats3["notified"] == 1


def test_state_price_drop(tmp_path):
    st = State(tmp_path / "s.json")
    st.mark(1, 1000, 70, notified=True)
    assert st.price_drop_pct(1, 850) == 15.0
    assert st.price_drop_pct(1, 1000) is None
    st.save()
    assert json.loads((tmp_path / "s.json").read_text())["seen"]["1"]["price"] == 1000
