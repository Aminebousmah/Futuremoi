"""Orchestration d'une campagne de veille.

    sources -> normalisation -> filtres/dedup -> enrichissement -> scoring -> base

Un seul point d'entree (`run_campaign`) pour que la CLI, un cron ou un test
partagent exactement le meme chemin de code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import Config, Profile
from ..models import JobOffer
from ..scrapers import PoliteClient, build_scrapers
from ..storage import Database
from .enrich import enrich_offer
from .filters import FilterReport, apply_filters
from .normalize import normalize_offer
from .score import rank

log = logging.getLogger(__name__)


@dataclass
class CampaignResult:
    fetched: int = 0
    kept: int = 0
    new_offers: int = 0
    updated_offers: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    filter_report: FilterReport | None = None
    offers: list[JobOffer] = field(default_factory=list)
    publiees_depuis: datetime | None = None

    @property
    def rejected_summary(self) -> str:
        return self.filter_report.summary() if self.filter_report else ""


def run_campaign(
    cfg: Config,
    profile: Profile,
    db: Database,
    *,
    sources: list[str] | None = None,
    dry_run: bool = False,
    tout: bool = False,
    progress=None,
) -> CampaignResult:
    """Execute une campagne complete.

    `progress` est un callable optionnel `(etape: str, detail: str) -> None`
    utilise par la CLI pour afficher l'avancement sans que ce module ne
    depende de rich.
    """
    def notify(step: str, detail: str = "") -> None:
        if progress:
            progress(step, detail)

    result = CampaignResult()
    raw_offers: list[JobOffer] = []

    with PoliteClient(cfg) as client:
        scrapers = build_scrapers(cfg, client, only=sources)
        if not scrapers:
            log.warning("Aucune source active. Verifiez la section `sources` de config.yaml.")
            return result

        for scraper in scrapers:
            notify("source", scraper.label)
            offers = scraper.run(cfg.search.keywords_any)
            result.per_source[scraper.name] = len(offers)
            raw_offers.extend(offers)

    result.fetched = len(raw_offers)
    notify("normalisation", f"{result.fetched} offres brutes")

    normalized = [enrich_offer(normalize_offer(o)) for o in raw_offers]

    notify("filtrage", "")
    depuis = None
    if cfg.filters.only_since_last_run and not tout:
        dernier = db.last_run_at()
        if dernier:
            # Marge : les sources indexent avec du retard, une annonce parue la
            # veille du dernier passage peut n'apparaitre qu'aujourd'hui.
            depuis = dernier - timedelta(days=cfg.filters.refresh_margin_days)
    result.publiees_depuis = depuis
    report = apply_filters(normalized, cfg, publiees_depuis=depuis)
    result.filter_report = report
    result.kept = len(report.kept)

    notify("scoring", f"{result.kept} offres retenues")
    scored = rank(report.kept, profile, cfg)
    result.offers = scored

    if not dry_run:
        new_count, updated = db.upsert_offers(scored)
        result.new_offers, result.updated_offers = new_count, updated
        db.log_run(
            sources=[s.name for s in scrapers],
            fetched=result.fetched,
            kept=result.kept,
            new_offers=new_count,
            detail=report.summary(),
        )

    return result
