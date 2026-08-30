"""Job boards remote a API JSON publique, sans authentification.

Ces quatre sources partagent la meme forme — un GET, une liste d'objets plats —
et ne demandent aucune logique propre au-dela du nom des champs. Les regrouper
evite quatre fichiers de trente lignes quasi identiques ; une source qui
demanderait une vraie mecanique (pagination, OAuth, HTML) garde son module.

robots.txt ne s'applique pas ici : ce sont des API documentees pour la
consommation programmatique (cf. `BaseScraper.respects_robots`).
"""

from __future__ import annotations

import ast
import html as html_module
from collections.abc import Iterator
from typing import Any

from ..models import ContractType, JobOffer, RemotePolicy
from ..pipeline.normalize import parse_datetime, strip_html
from .base import BaseScraper, register


def _tags(valeur: Any) -> list[str]:
    """Les tags arrivent en liste, en chaine JSON ou en repr Python."""
    if isinstance(valeur, list):
        return [str(t) for t in valeur][:25]
    if isinstance(valeur, str) and valeur.strip():
        try:
            return [str(t) for t in ast.literal_eval(valeur)][:25]
        except (ValueError, SyntaxError):
            return [t.strip(" '\"") for t in valeur.strip("[]").split(",") if t.strip()][:25]
    return []


class _RemoteApiScraper(BaseScraper):
    """Socle commun : un appel, une liste, une conversion."""

    api_url: str = ""
    liste_cle: str | None = None   # cle contenant la liste, None si racine
    respects_robots = False

    def params(self) -> dict[str, Any]:
        return {}

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        payload = self.get_json(self.api_url, params=self.params())
        if self.liste_cle:
            elements = payload.get(self.liste_cle, []) if isinstance(payload, dict) else []
        else:
            elements = payload if isinstance(payload, list) else []
        for brut in elements:
            if isinstance(brut, dict):
                offre = self.convertir(brut)
                if offre:
                    yield offre

    def convertir(self, brut: dict) -> JobOffer | None:  # pragma: no cover - abstrait
        raise NotImplementedError


@register
class JobicyScraper(_RemoteApiScraper):
    name = "jobicy"
    label = "Jobicy (remote)"
    homepage = "https://jobicy.com"
    api_url = "https://jobicy.com/api/v2/remote-jobs"
    liste_cle = "jobs"

    def params(self) -> dict[str, Any]:
        return {"industry": self._cfg("industry", "data-science"),
                "count": int(self._cfg("count", 50))}

    def convertir(self, brut: dict) -> JobOffer | None:
        titre = brut.get("jobTitle")
        if not titre:
            return None
        description = brut.get("jobDescription") or brut.get("jobExcerpt") or ""
        return JobOffer(
            source=self.name,
            source_id=str(brut.get("id", "")),
            url=brut.get("url", ""),
            title=html_module.unescape(titre),
            company=html_module.unescape(str(brut.get("companyName", ""))),
            description=strip_html(description),
            location=brut.get("jobGeo") or "Remote",
            remote=RemotePolicy.FULL_REMOTE,
            contract=(ContractType.FREELANCE
                      if "contract" in " ".join(_tags(brut.get("jobType"))).lower()
                      else ContractType.UNKNOWN),
            skills=_tags(brut.get("jobIndustry")),
            published_at=parse_datetime(brut.get("pubDate")),
        )


@register
class HimalayasScraper(_RemoteApiScraper):
    name = "himalayas"
    label = "Himalayas (remote)"
    homepage = "https://himalayas.app"
    api_url = "https://himalayas.app/jobs/api"
    liste_cle = "jobs"

    def params(self) -> dict[str, Any]:
        return {"limit": int(self._cfg("limit", 50))}

    def convertir(self, brut: dict) -> JobOffer | None:
        titre = brut.get("title")
        if not titre:
            return None
        lieux = brut.get("locationRestrictions") or []
        return JobOffer(
            source=self.name,
            source_id=str(brut.get("guid") or brut.get("companySlug", "")),
            url=brut.get("applicationLink") or brut.get("url", ""),
            title=titre,
            company=brut.get("companyName", ""),
            description=strip_html(brut.get("description") or brut.get("excerpt") or ""),
            location=", ".join(str(x) for x in lieux) or "Remote",
            remote=RemotePolicy.FULL_REMOTE,
            contract=(ContractType.FREELANCE
                      if "contract" in str(brut.get("employmentType", "")).lower()
                      else ContractType.UNKNOWN),
            skills=_tags(brut.get("categories")),
            published_at=parse_datetime(brut.get("pubDate") or brut.get("publishedDate")),
        )


@register
class ArbeitnowScraper(_RemoteApiScraper):
    name = "arbeitnow"
    label = "Arbeitnow (Europe)"
    homepage = "https://www.arbeitnow.com"
    api_url = "https://www.arbeitnow.com/api/job-board-api"
    liste_cle = "data"

    def convertir(self, brut: dict) -> JobOffer | None:
        titre = brut.get("title")
        if not titre:
            return None
        return JobOffer(
            source=self.name,
            source_id=str(brut.get("slug", "")),
            url=brut.get("url", ""),
            title=titre,
            company=brut.get("company_name", ""),
            description=strip_html(brut.get("description", "")),
            location=brut.get("location") or "",
            remote=RemotePolicy.FULL_REMOTE if brut.get("remote") else RemotePolicy.UNKNOWN,
            skills=_tags(brut.get("tags")),
            published_at=parse_datetime(brut.get("created_at")),
        )


@register
class WorkingNomadsScraper(_RemoteApiScraper):
    name = "workingnomads"
    label = "Working Nomads (remote)"
    homepage = "https://www.workingnomads.com"
    api_url = "https://www.workingnomads.com/api/exposed_jobs/"
    liste_cle = None   # la reponse est une liste a la racine

    def convertir(self, brut: dict) -> JobOffer | None:
        titre = brut.get("title")
        if not titre:
            return None
        return JobOffer(
            source=self.name,
            source_id=str(brut.get("url", ""))[-60:],
            url=brut.get("url", ""),
            title=titre,
            company=brut.get("company_name", ""),
            description=strip_html(brut.get("description", "")),
            location=brut.get("location") or "Remote",
            remote=RemotePolicy.FULL_REMOTE,
            skills=[t.strip() for t in str(brut.get("tags", "")).split(",") if t.strip()][:25],
            published_at=parse_datetime(brut.get("pub_date")),
        )
