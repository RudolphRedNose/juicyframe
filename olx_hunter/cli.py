"""CLI: olx-hunter scan | analyze <url|id> | test-notify | dump-fixture."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import Config, load_config
from .llm import LlmAnalyst, safe_analyze
from .models import Offer
from .notify import NtfyNotifier
from .olx_client import OlxBlockedError, OlxClient, OlxError
from .report import format_rejection, format_report
from .scoring import Scorer
from .state import State

log = logging.getLogger("olx_hunter")


# ----------------------------------------------------------------------------------------
# Składanie zależności
# ----------------------------------------------------------------------------------------
def build_analyst(cfg: Config, use_llm: bool) -> Optional[LlmAnalyst]:
    if not use_llm:
        return None
    try:
        return LlmAnalyst(cfg.prompt_path, model=cfg.llm.get("model", "claude-opus-5-5"),
                          effort=cfg.llm.get("effort", "medium"))
    except Exception as exc:  # noqa: BLE001 - brak klucza, brak pliku promptu itp.
        log.warning("LLM niedostępny (%s) - używam samej heurystyki.", exc)
        return None


def build_notifier(cfg: Config, dry_run: bool) -> Optional[NtfyNotifier]:
    if dry_run:
        return None
    n = cfg.ntfy
    if not n.get("topic"):
        log.warning("Brak NTFY_TOPIC - powiadomienia wyłączone (tryb dry-run).")
        return None
    return NtfyNotifier(n["server"], n["topic"], n.get("token", ""),
                        priority_default=int(n.get("priority_default", 4)),
                        priority_urgent=int(n.get("priority_urgent", 5)))


def load_fixture(path: Path) -> list[Offer]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("data") if isinstance(payload, dict) else payload
    return [Offer.from_api(item) for item in items]


# ----------------------------------------------------------------------------------------
# Główny przebieg skanu
# ----------------------------------------------------------------------------------------
def run_scan(cfg: Config, use_llm: bool = True, dry_run: bool = False, fixture: Optional[Path] = None,
             verbose: bool = False, force: bool = False) -> dict[str, Any]:
    scorer = Scorer(cfg.rules, cfg.filters)
    state = State(cfg.state_path)
    analyst = build_analyst(cfg, use_llm)
    notifier = build_notifier(cfg, dry_run)
    min_score = int(cfg.filters.get("min_score", 55))
    notify_min_verdict = int(cfg.llm.get("notify_min_verdict", 6))
    max_llm = int(cfg.llm.get("max_offers_per_run", 8))
    price_drop_pct = float(cfg.state.get("price_drop_pct", 10))
    # Fixture to statyczny zrzut - filtr świeżości zależny od bieżącej daty go nie dotyczy.
    max_age_hours = 0.0 if fixture else float(cfg.olx.get("max_age_hours", 0) or 0)

    stats = {"fetched": 0, "rejected": 0, "below_threshold": 0, "seen": 0, "analyzed": 0, "notified": 0, "errors": 0}
    reports: list[str] = []
    rejections: list[str] = []

    def offers_for(search: dict[str, Any]) -> Iterable[Offer]:
        if fixture:
            return load_fixture(fixture)
        client = OlxClient(cfg.olx.get("base_url", "https://www.olx.pl"),
                           request_delay=float(cfg.olx.get("request_delay", 1.5)))
        return client.search(search["query"], search.get("price_min"), search.get("price_max"),
                             search.get("category_id"), max_pages=int(cfg.olx.get("max_pages", 2)))

    searches = cfg.searches if not fixture else (cfg.searches[:1] or [{"name": "fixture", "query": ""}])
    if fixture:
        searches = [{"name": "fixture", "query": "fixture"}]  # fixture nie podlega must_match_any

    candidates: list[tuple[Offer, Any, dict[str, Any], Optional[float]]] = []
    processed_ids: set[int] = set()
    for search in searches:
        log.info("Wyszukiwanie: %s (%s)", search.get("name", "?"), search.get("query"))
        try:
            for offer in offers_for(search):
                if offer.id in processed_ids:
                    continue
                processed_ids.add(offer.id)
                stats["fetched"] += 1
                if max_age_hours and offer.age_hours is not None and offer.age_hours > max_age_hours:
                    continue
                drop = state.price_drop_pct(offer.id, offer.price)
                if state.is_seen(offer.id) and not force and (drop is None or drop < price_drop_pct):
                    stats["seen"] += 1
                    state.mark(offer.id, offer.price, state.get(offer.id).get("score", 0), notified=False)
                    continue
                h = scorer.score(offer, search)
                if h.rejected:
                    stats["rejected"] += 1
                    rejections.append(format_rejection(offer, h))
                    state.mark(offer.id, offer.price, 0, notified=False)
                    continue
                if h.score < min_score:
                    stats["below_threshold"] += 1
                    rejections.append(format_rejection(offer, h))
                    state.mark(offer.id, offer.price, h.score, notified=False)
                    continue
                candidates.append((offer, h, search, drop))
        except OlxBlockedError as exc:
            log.error("%s", exc)
            stats["errors"] += 1
            break
        except OlxError as exc:
            log.error("Błąd OLX dla '%s': %s", search.get("name"), exc)
            stats["errors"] += 1

    # Najlepsze wg heurystyki idą do LLM pierwsze (limit kosztów).
    candidates.sort(key=lambda c: c[1].score, reverse=True)
    for idx, (offer, h, _search, drop) in enumerate(candidates):
        use_analyst = analyst if idx < max_llm else None
        analysis = safe_analyze(use_analyst, offer, h)
        stats["analyzed"] += 1
        report = format_report(offer, analysis, h)
        reports.append(report)
        notified = False
        if analysis.verdict_score >= notify_min_verdict:
            note = f"Spadek ceny o {drop:.0f}%" if drop else ""
            if notifier:
                try:
                    notifier.notify_deal(offer, analysis, note)
                    notified = True
                    stats["notified"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.error("ntfy nie działa: %s", exc)
                    stats["errors"] += 1
            else:
                log.info("[dry-run] Powiadomienie: %s", analysis.title_and_price)
                stats["notified"] += 1
        state.mark(offer.id, offer.price, h.score, notified=notified, verdict=analysis.verdict_score)

    state.save()

    # --- wyjście ---
    print(f"\n=== OLX Hunter: {stats['fetched']} ofert, {len(candidates)} kandydatów, "
          f"{stats['notified']} powiadomień, odrzucono {stats['rejected']}, "
          f"poniżej progu {stats['below_threshold']}, znane {stats['seen']}, błędy {stats['errors']} ===\n")
    for r in reports:
        print(r)
        print("-" * 80)
    if verbose and rejections:
        print("\nOdrzucone / poniżej progu:")
        print("\n".join(rejections))

    if notifier and cfg.ntfy.get("send_summary"):
        try:
            notifier.notify_summary(
                f"Ofert: {stats['fetched']}, kandydatów: {len(candidates)}, powiadomień: {stats['notified']}, "
                f"błędów: {stats['errors']}")
        except Exception as exc:  # noqa: BLE001
            log.error("ntfy (podsumowanie) nie działa: %s", exc)
    return stats


def run_analyze(cfg: Config, target: str, use_llm: bool = True, notify: bool = False) -> int:
    scorer = Scorer(cfg.rules, cfg.filters)
    if target.endswith(".json") and Path(target).exists():
        offers = load_fixture(Path(target))
    else:
        client = OlxClient(cfg.olx.get("base_url", "https://www.olx.pl"))
        offer_id = client.resolve_offer_id(target)
        offers = [client.get_offer(offer_id)]
    analyst = build_analyst(cfg, use_llm)
    notifier = build_notifier(cfg, dry_run=not notify)
    for offer in offers:
        h = scorer.score(offer)
        if h.rejected:
            print(format_rejection(offer, h))
            continue
        analysis = safe_analyze(analyst, offer, h)
        print(format_report(offer, analysis, h))
        print("-" * 80)
        if notifier:
            notifier.notify_deal(offer, analysis)
    return 0


# ----------------------------------------------------------------------------------------
# argparse
# ----------------------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="olx-hunter", description="Łowca okazji na OLX z powiadomieniami ntfy.")
    parser.add_argument("--config", help="ścieżka do config.yaml")
    parser.add_argument("--rules", help="ścieżka do rules.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", parents=[common], help="przeskanuj wszystkie wyszukiwania z config.yaml")
    p_scan.add_argument("--no-llm", action="store_true", help="tylko heurystyka, bez Claude")
    p_scan.add_argument("--dry-run", action="store_true", help="nie wysyłaj ntfy, tylko wypisz")
    p_scan.add_argument("--fixture", help="plik JSON z ofertami (format API OLX) zamiast sieci")
    p_scan.add_argument("--force", action="store_true", help="ignoruj pamięć widzianych ofert")

    p_an = sub.add_parser("analyze", parents=[common], help="przeanalizuj jedną ofertę (URL, id lub plik JSON)")
    p_an.add_argument("target")
    p_an.add_argument("--no-llm", action="store_true")
    p_an.add_argument("--notify", action="store_true", help="wyślij też na ntfy")

    p_test = sub.add_parser("test-notify", parents=[common], help="wyślij testowe powiadomienie ntfy")
    p_test.add_argument("--message", default="OLX Hunter działa. To jest test kanału ntfy.")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config, args.rules)

    if args.cmd == "scan":
        run_scan(cfg, use_llm=cfg.llm_enabled and not args.no_llm, dry_run=args.dry_run,
                 fixture=Path(args.fixture) if args.fixture else None, verbose=args.verbose, force=args.force)
        return 0
    if args.cmd == "analyze":
        return run_analyze(cfg, args.target, use_llm=cfg.llm_enabled and not args.no_llm, notify=args.notify)
    if args.cmd == "test-notify":
        notifier = build_notifier(cfg, dry_run=False)
        if not notifier:
            print("Ustaw NTFY_TOPIC (np. w .env).", file=sys.stderr)
            return 2
        notifier.publish("OLX Hunter - test", args.message, priority=3, tags=["white_check_mark"])
        print(f"Wysłano na {cfg.ntfy['server']}/{cfg.ntfy['topic']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
