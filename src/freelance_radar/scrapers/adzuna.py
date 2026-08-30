"""Adzuna : agregateur multi-pays, API officielle gratuite (cle requise).

Inscription : https://developer.adzuna.com  ->  ADZUNA_APP_ID / ADZUNA_APP_KEY
"""

from __future__ import annotations

from collections.abc import Iterator

from ..config import env
from ..models import ContractType, JobOffer
from ..pipeline.normalize import parse_datetime, strip_html
from .base import BaseScraper, register

API_TEMPLATE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


@register
class AdzunaScraper(BaseScraper):
    name = "adzuna"
    label = "Adzuna (agregateur)"
    homepage = "https://www.adzuna.fr"
    respects_robots = False  # API officielle, acces par cle

    def is_configured(self) -> bool:
        return bool(env("ADZUNA_APP_ID") and env("ADZUNA_APP_KEY"))

    def missing_requirement(self) -> str:
        return "ADZUNA_APP_ID / ADZUNA_APP_KEY absents du .env"

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        country = str(self._cfg("country", "fr"))
        max_pages = int(self._cfg("max_pages", 3))
        per_page = int(self._cfg("results_per_page", 50))
        # Adzuna traite `what` comme un ET : on interroge terme par terme.
        requetes = self.queries()

        for requete in requetes:
            for page in range(1, max_pages + 1):
                payload = self.get_json(
                    API_TEMPLATE.format(country=country, page=page),
                    params={
                        "app_id": env("ADZUNA_APP_ID"),
                        "app_key": env("ADZUNA_APP_KEY"),
                        "what": requete,
                        "results_per_page": per_page,
                        "content-type": "application/json",
                        # Pas de `contract_type` ici : l'API le rejette en 400,
                        # quelle que soit sa valeur. Le champ revient en revanche
                        # dans chaque resultat, donc le tri se fait a la lecture.
                        "max_days_old": self.cfg.filters.max_age_days or 30,
                    },
                )
                results = payload.get("results", [])
                if not results:
                    break
                for raw in results:
                    offer = self._parse(raw)
                    if offer:
                        yield offer

    def _parse(self, raw: dict) -> JobOffer | None:
        title = raw.get("title")
        if not title:
            return None
        location = raw.get("location", {}) or {}
        company = raw.get("company", {}) or {}
        # Adzuna n'expose que deux valeurs, et l'absence est frequente (~50 %).
        # Mapper `permanent` explicitement evite de garder des CDI sous
        # l'etiquette "inconnu", que le pipeline conserve par defaut.
        contract = {
            "contract": ContractType.FREELANCE,
            "permanent": ContractType.CDI,
        }.get(str(raw.get("contract_type", "")).lower(), ContractType.UNKNOWN)
        return JobOffer(
            source=self.name,
            source_id=str(raw.get("id", "")),
            url=raw.get("redirect_url", ""),
            title=strip_html(title),
            company=company.get("display_name", ""),
            description=strip_html(raw.get("description", "")),
            location=location.get("display_name", ""),
            contract=contract,
            published_at=parse_datetime(raw.get("created")),
        )
