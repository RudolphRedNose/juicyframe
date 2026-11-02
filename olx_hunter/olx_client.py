"""Klient nieoficjalnego API OLX (to samo, z którego korzysta strona www).

Endpoint: GET https://www.olx.pl/api/v1/offers/?query=...&offset=...&limit=40
Uwaga: OLX (CloudFront) blokuje ruch z części adresów IP centrów danych oraz klientów HTTP
o "nieprzeglądarkowym" odcisku TLS (requests/curl dostają 403 nawet z domowego IP).
Dlatego domyślnie używamy curl_cffi z impersonacją Chrome; bez niego - zwykłe requests.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Iterator, Optional

import requests

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - opcjonalna zależność
    cffi_requests = None

from .models import Offer

log = logging.getLogger(__name__)

NET_ERRORS: tuple[type[BaseException], ...] = (requests.RequestException,)
if cffi_requests is not None:
    NET_ERRORS += (cffi_requests.RequestsError,)


def _default_session() -> Any:
    if cffi_requests is not None and os.environ.get("OLX_IMPERSONATE", "chrome") not in ("", "0"):
        return cffi_requests.Session(impersonate=os.environ.get("OLX_IMPERSONATE", "chrome"))
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class OlxError(RuntimeError):
    pass


class OlxBlockedError(OlxError):
    """OLX odrzucił żądanie (403) - zwykle blokada IP / CloudFront."""


class OlxClient:
    def __init__(self, base_url: str = "https://www.olx.pl", request_delay: float = 1.5,
                 session: Optional[requests.Session] = None, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = session or _default_session()
        # User-Agent ustawia impersonacja (musi pasować do odcisku TLS) albo _default_session.
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        })
        cookie = os.environ.get("OLX_COOKIE")
        if cookie:
            self.session.headers["Cookie"] = cookie
        self._last_request = 0.0

    # --- niskopoziomowe ---
    def _throttle(self) -> None:
        wait = self.request_delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        self._throttle()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except NET_ERRORS as exc:
                if attempt == 2:
                    raise OlxError(f"Błąd sieci przy {url}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 403:
                raise OlxBlockedError(
                    "OLX zwrócił 403 (CloudFront). Uruchom skaner z innego IP (PL/domowe) "
                    "lub ustaw OLX_COOKIE z sesji przeglądarki."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise OlxError(f"OLX HTTP {resp.status_code} dla {resp.url}")
            try:
                return resp.json()
            except ValueError as exc:
                raise OlxError(f"OLX zwrócił nie-JSON dla {resp.url}") from exc
        raise OlxError(f"OLX: wyczerpano próby dla {url}")

    # --- wysokopoziomowe ---
    def search(self, query: str, price_min: Optional[float] = None, price_max: Optional[float] = None,
               category_id: Optional[int] = None, max_pages: int = 2, page_size: int = 40,
               sort: str = "created_at:desc") -> Iterator[Offer]:
        """Iteruje po ofertach pasujących do zapytania, od najnowszych."""
        params: dict[str, Any] = {
            "query": query,
            "limit": page_size,
            "sort_by": sort,
            "filter_refiners": "spell_checker",
        }
        if price_min is not None:
            params["filter_float_price:from"] = int(price_min)
        if price_max is not None:
            params["filter_float_price:to"] = int(price_max)
        if category_id:
            params["category_id"] = category_id

        for page in range(max_pages):
            params["offset"] = page * page_size
            payload = self._get("/api/v1/offers/", params)
            items = payload.get("data") or []
            if not items:
                return
            for item in items:
                try:
                    yield Offer.from_api(item)
                except Exception as exc:  # noqa: BLE001 - jeden zły rekord nie zatrzymuje skanu
                    log.warning("Pominięto ofertę (błąd parsowania): %s", exc)
            if len(items) < page_size:
                return

    def get_offer(self, offer_id: int) -> Offer:
        payload = self._get(f"/api/v1/offers/{offer_id}/")
        data = payload.get("data") or payload
        return Offer.from_api(data)

    def resolve_offer_id(self, url: str) -> int:
        """Z URL ogłoszenia (…-IDxxxxx.html) wyciąga numeryczne id, pobierając stronę HTML."""
        if url.isdigit():
            return int(url)
        self._throttle()
        resp = self.session.get(url, timeout=self.timeout, headers={"Accept": "text/html"})
        if resp.status_code == 403:
            raise OlxBlockedError("OLX zwrócił 403 przy pobieraniu strony ogłoszenia.")
        resp.raise_for_status()
        html = resp.text
        for pattern in (r'"offer_id"\s*:\s*(\d{6,})', r'"id"\s*:\s*(\d{6,})\s*,\s*"url"', r'data-ad-id="(\d{6,})"',
                        r'adId\\?"?\s*:\s*\\?"?(\d{6,})'):
            m = re.search(pattern, html)
            if m:
                return int(m.group(1))
        raise OlxError(f"Nie udało się znaleźć id oferty w stronie: {url}")
