"""Powiadomienia push przez ntfy (https://ntfy.sh lub własny serwer).

Publikujemy JSON-em na root serwera - nagłówki HTTP w ntfy muszą być ASCII, a JSON przyjmuje
polskie znaki w tytule bez kodowania RFC 2047.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .models import Analysis, Offer

log = logging.getLogger(__name__)


class NtfyNotifier:
    def __init__(self, server: str, topic: str, token: str = "", priority_default: int = 4,
                 priority_urgent: int = 5, session: Optional[requests.Session] = None, timeout: float = 15.0):
        if not topic:
            raise ValueError("Brak tematu ntfy - ustaw NTFY_TOPIC.")
        self.server = server.rstrip("/")
        self.topic = topic
        self.token = token
        self.priority_default = priority_default
        self.priority_urgent = priority_urgent
        self.session = session or requests.Session()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def publish(self, title: str, message: str, priority: Optional[int] = None,
                tags: Optional[list[str]] = None, click: Optional[str] = None,
                actions: Optional[list[dict[str, Any]]] = None, markdown: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {
            "topic": self.topic,
            "title": title[:250],
            "message": message[:4000],
            "priority": priority or self.priority_default,
            "markdown": markdown,
        }
        if tags:
            body["tags"] = tags
        if click:
            body["click"] = click
        if actions:
            body["actions"] = actions
        resp = self.session.post(self.server, json=body, headers=self._headers(), timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"ntfy HTTP {resp.status_code}: {resp.text[:200]}")
        log.info("ntfy: wysłano '%s' (priorytet %s)", title, body["priority"])
        try:
            return resp.json()
        except ValueError:
            return {}

    def notify_deal(self, offer: Offer, analysis: Analysis, extra_note: str = "") -> dict[str, Any]:
        from .report import format_push_message

        urgent = analysis.verdict_score >= 8
        price = f"{offer.price:.0f} zł" if offer.price else "cena do uzgodnienia"
        title = f"[{analysis.verdict_score}/10 {analysis.verdict}] {offer.title[:80]} - {price}"
        tags = ["moneybag" if urgent else "mag", "computer"]
        if analysis.scam_risk == "wysokie":
            tags.append("warning")
        if extra_note:
            tags.append("chart_with_downwards_trend")
        return self.publish(
            title=title,
            message=format_push_message(offer, analysis, extra_note),
            priority=self.priority_urgent if urgent else self.priority_default,
            tags=tags,
            click=offer.url,
            actions=[{"action": "view", "label": "Otwórz ofertę", "url": offer.url}],
        )

    def notify_summary(self, text: str, title: str = "OLX Hunter - podsumowanie skanu") -> dict[str, Any]:
        return self.publish(title=title, message=text, priority=2, tags=["clipboard"])
