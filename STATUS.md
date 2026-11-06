# OLX Hunter: stan projektu (zatrzymany 2026-11-06)

Projekt wstrzymany przez Pawła ("fajny pomysł, ale na razie nie działa"). Ten plik mówi, gdzie stanęliśmy.

## Co jest gotowe
- Cały kod aplikacji (skaner OLX, heurystyka z `rules.yaml`, analiza Claude, ntfy, pamięć ofert), README, workflow GitHub Actions i jednostki systemd w `deploy/`.
- Testy offline na fixture (`tests/`), przechodziły przy pierwszym commicie.
- Poprawka z MacBooka (commit 4b5ce1e): OLX zwracał 403 nawet z domowego IP, bo CloudFront odrzuca nieprzeglądarkowy odcisk TLS klientów `requests` i `curl`. Klient używa teraz `curl_cffi` z impersonacją Chrome (zmienna `OLX_IMPERSONATE`), a bez niego wraca do `requests`. Poprawiono też wiek konta sprzedawcy (liczony w chwili wystawienia oferty), pominięcie filtra świeżości dla fixture i flagę `-v` przed podkomendą.

## Co NIE zostało potwierdzone
- Czy po poprawce `curl_cffi` skan na żywo faktycznie zwraca oferty z OLX.
- Czy powiadomienia ntfy dochodzą na telefon. W chmurze ntfy.sh odrzucił test (dzienny limit współdzielonego IP), z domowego łącza nie sprawdzano.
- Czy cokolwiek zostało wdrożone na serwerze Pablo. SSH z MacBooka jako `Apple` kończył się `Permission denied (publickey)`. Znane adresy Pablo: 192.168.1.166 (LAN), 100.117.144.85 (Tailscale).
- Ścieżka LLM była testowana tylko z atrapą klienta, bez prawdziwego klucza API.
- Ceny w `rules.yaml` są szacunkowe i wymagają weryfikacji pod aktualny rynek.

## Jak wrócić do tematu
1. `git clone -b claude/olx-market-opportunities-d8thnf https://github.com/RudolphRedNose/juicyframe.git olx-hunter`
2. `pip install -r requirements-dev.txt && pytest -q`
3. Z domowej sieci: `python -m olx_hunter -v scan --dry-run --no-llm`. Jeśli nadal 403, ustaw `OLX_COOKIE` (nagłówek Cookie z Chrome) i sprawdź wersję `curl_cffi`.
4. Uzupełnij `.env` (`NTFY_TOPIC`, `ANTHROPIC_API_KEY`) i uruchom `python -m olx_hunter test-notify`.
5. Dopiero potem wdrożenie na Pablo (systemd timer z `deploy/`), po naprawieniu logowania SSH.

## Uwagi z sesji
- Remote Control nie przyjął folderu ze spacjami i polskimi znakami w ścieżce. Działał dopiero `~/olx-hunter` na MacBooku.
- Zaplanowane sprawdzanie sesji MacBooka zostało usunięte, sesja zatrzymana.
