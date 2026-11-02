# OLX Hunter

Łowca okazji na OLX dla inżyniera / twórcy cyfrowego. Skanuje zdefiniowane wyszukiwania, odsiewa
złom i scamy tanią heurystyką, najlepsze kandydatury analizuje Claude w roli "agenta analitycznego",
a perełki lecą jako push na telefon przez [ntfy](https://ntfy.sh).

```
OLX API ──> heurystyka (rules.yaml) ──> Claude (prompts/analyst.md) ──> ntfy push
             │ profil cenowy, scam,          │ raport: Tytuł/Cena, Spec,
             │ ukryte koszty, archiwalia     │ Plusy, Pułapki, Werdykt 1-10
             └─ odrzuca większość ofert      └─ tylko gdy werdykt >= 6
```

## Co robi

1. **Wyszukiwanie** - lista zapytań w `config.yaml` (Tiny/Micro/Mini z leasingu, CPU o wysokim
   single-core, GPU, audio/wideo). Każde ma widełki cenowe i słowa wymagane w tytule.
2. **Heurystyka** (`rules.yaml`, 0-100 pkt):
   - profil referencyjny z cenami `fair` / `good` / `scam` - cena poniżej `good` to bonus, poniżej `scam` to czerwona flaga;
   - odrzucanie przestarzałej architektury (Xeon v1-v3, Celeron/Pentium/Atom, Core 2-6 gen, DDR3, N100/J4125), chyba że cena jest symboliczna (`filters.symbolic_price`);
   - ukryte koszty (brak zasilacza / dysku / RAM, DDR5, brak gwarancji, blokada BIOS);
   - sygnały scamu (przedpłata poza OLX, WhatsApp, "pilnie", konto założone dziś, brak zdjęć);
   - plusy (faktura VAT, gwarancja, poleasingowy, Przesyłka OLX).
3. **Analiza LLM** - kandydaci z wynikiem >= `filters.min_score` trafiają do Claude
   (domyślnie `claude-opus-5-5`, limit `llm.max_offers_per_run` na skan). Model dostaje ofertę
   i wynik heurystyki, zwraca JSON w formacie raportu okazji. Gdy brak klucza API lub API padnie,
   raport buduje się z samej heurystyki (werdykt max 8/10).
4. **Powiadomienie ntfy** - tytuł `[8/10 Kupować] Lenovo M920q ... - 450 zł`, treść z podsumowaniem,
   spec/plusy/minusy, klik otwiera ofertę. Werdykt >= 8 idzie z priorytetem `urgent`.
5. **Pamięć** (`.olx_hunter_state.json`) - znane oferty nie są powtarzane, chyba że cena spadła
   o >= `state.price_drop_pct` procent.

## Szybki start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # uzupełnij NTFY_TOPIC i ANTHROPIC_API_KEY

python -m olx_hunter test-notify                       # test kanału ntfy
python -m olx_hunter -v scan --dry-run --no-llm        # sucha próba, sama heurystyka
python -m olx_hunter scan                              # pełny skan + Claude + push
python -m olx_hunter analyze "https://www.olx.pl/d/oferta/...html"   # jedna oferta
python -m olx_hunter scan --fixture tests/fixtures/offers.json --dry-run --no-llm  # offline
```

### ntfy na telefonie

1. Zainstaluj aplikację ntfy (Android / iOS) albo otwórz `https://ntfy.sh/<twoj-topic>`.
2. Wymyśl trudny do zgadnięcia topic, np. `olx-pawel-8f2a91` (tematy na ntfy.sh są publiczne
   dla każdego, kto zna nazwę). Wpisz go w `.env` jako `NTFY_TOPIC` i zasubskrybuj w aplikacji.
3. Własny serwer ntfy z logowaniem: ustaw `NTFY_SERVER` i `NTFY_TOKEN`.

## Konfiguracja

| Plik | Co zawiera |
|------|------------|
| `config.yaml` | wyszukiwania, progi, model LLM, ntfy, pamięć |
| `rules.yaml` | ceny referencyjne, wzorce archiwaliów, ukryte koszty, frazy scamu, plusy |
| `prompts/analyst.md` | prompt systemowy agenta analitycznego (persona + format raportu) |
| `.env` | `NTFY_TOPIC`, `NTFY_SERVER`, `NTFY_TOKEN`, `ANTHROPIC_API_KEY`, `OLX_COOKIE`, `OLX_LLM=0` |

Ceny w `rules.yaml` to orientacyjne wartości rynku wtórnego w PL. Aktualizuj je co kilka miesięcy,
bo od nich zależy, co heurystyka uzna za okazję, a co za scam.

## Uruchamianie cyklicznie

**OLX blokuje ruch z większości centrów danych (CloudFront 403)**, w tym często z runnerów
GitHub Actions i ze środowiska, w którym powstał ten kod. Najpewniej działa z domowego / polskiego IP:

- **systemd timer** (Linux, Raspberry Pi, mini PC): skopiuj `deploy/olx-hunter.service`
  i `deploy/olx-hunter.timer` do `/etc/systemd/system/`, dopasuj ścieżki, potem
  `systemctl enable --now olx-hunter.timer`.
- **cron**: `*/20 * * * * cd /opt/olx-hunter && .venv/bin/python -m olx_hunter scan >> scan.log 2>&1`
- **GitHub Actions**: `.github/workflows/scan.yml` (co 30 min, sekrety `NTFY_TOPIC`,
  `ANTHROPIC_API_KEY`, opcjonalnie `NTFY_TOKEN`, `OLX_COOKIE`; zmienna `OLX_RUNNER` pozwala
  wskazać self-hosted runner w PL). Jeśli log pokazuje 403, to blokada IP, nie błąd kodu.

Gdy OLX zacznie wymagać ciasteczek, skopiuj nagłówek `Cookie` z sesji przeglądarki do `OLX_COOKIE`.

## Koszty LLM

Jedna analiza to ok. 2-3 tys. tokenów wejścia (prompt systemowy jest cache'owany) i kilkaset
wyjścia. Przy `max_offers_per_run: 8` i skanie co 20 minut górna granica to ~600 analiz dziennie,
realnie kilkanaście, bo pamięć odsiewa znane oferty. `OLX_LLM=0` lub `--no-llm` wyłącza model.

Kod dla `claude-opus-5-5` ma włączony server-side fallback (`fallbacks: "default"`): jeśli
klasyfikator bezpieczeństwa odrzuci żądanie, API samo ponawia je na modelu zapasowym.
Usuń `betas`/`fallbacks` w `olx_hunter/llm.py`, jeśli tego nie chcesz.

## Testy

```bash
pip install -r requirements-dev.txt
pytest -q
```

Testy używają `tests/fixtures/offers.json` (5 ofert: perełka M920q, scam na RTX 3080,
archiwalny Xeon, Dell bez zasilacza i dysku, tani N100) i nie dotykają sieci ani API.
