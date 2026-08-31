"""Collective.work : missions freelance, en direct plutôt que via Adzuna.

Pourquoi cette source existe
----------------------------
Collective.work publiait déjà 31 des 118 offres que le radar recevait
d'Adzuna — mais tronquées à 500 caractères, la limite de l'API Adzuna. Un cas
mesuré : une annonce exigeant « Microsoft Fabric souhaitable » dont la
mention tombait hors de l'extrait, donc absente du CV composé.

Le texte complet est hors de portée côté Adzuna : leur API n'a pas de champ
plus long, et leur site répond 403 à tout client automatisé. En revanche
l'éditeur, lui, publie ses missions en clair. On va donc les chercher à la
source.

    description : 500 caractères chez Adzuna
                  786 à 5441 caractères ici, médiane 1831

Ce que la source apporte en plus
--------------------------------
  * `workPreferences` déclare le rythme (HYBRID / ON_SITE / REMOTE), là où
    il fallait le deviner dans le texte ;
  * `budgetBrief` porte parfois le TJM annoncé ;
  * `expirationDate` dit jusqu'à quand la mission est ouverte ;
  * `job.applicationTypeValue` donne l'adresse de candidature directe.

robots.txt (vérifié le 31/08/2026) n'interdit que `/style-guide`. Les
missions sont rendues côté serveur dans `__NEXT_DATA__` : aucune API privée
n'est sollicitée, aucune authentification n'est contournée.

Volume : 6360 missions au catalogue, 30 par page. Le pipeline filtre en aval,
mais il serait impoli de tout parcourir à chaque campagne — d'où `max_pages`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

from ..models import ContractType, JobOffer, RemotePolicy
from ..pipeline.normalize import parse_daily_rate, parse_datetime, strip_html
from .base import BaseScraper, register

log = logging.getLogger(__name__)

BASE = "https://www.collective.work"
LISTING = f"{BASE}/jobs/fr"

_NEXT_DATA = re.compile(
    r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)

# `workPreferences` est une liste : une mission peut accepter plusieurs
# rythmes. Le plus favorable l'emporte, c'est ce que le candidat negociera.
_RYTHMES = {
    "REMOTE": RemotePolicy.FULL_REMOTE,
    "FULL_REMOTE": RemotePolicy.FULL_REMOTE,
    "HYBRID": RemotePolicy.HYBRID,
    "ON_SITE": RemotePolicy.ONSITE,
    "ONSITE": RemotePolicy.ONSITE,
}
_ORDRE = (RemotePolicy.FULL_REMOTE, RemotePolicy.HYBRID, RemotePolicy.ONSITE)


def extraire_missions(html: str) -> list[dict[str, Any]]:
    """Rend les missions embarquees dans `__NEXT_DATA__`.

    Le chemin est profond et peut bouger : toute absence rend une liste vide
    plutot que de lever, une source qui change de forme ne devant pas casser
    la campagne.
    """
    bloc = _NEXT_DATA.search(html)
    if not bloc:
        return []
    try:
        donnees = json.loads(bloc.group(1))
        requetes = donnees["props"]["pageProps"]["dehydratedState"]["queries"]
        return list(requetes[0]["state"]["data"]["results"]["projects"])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        log.warning("Collective.work : structure inattendue (%s)", exc)
        return []


def teletravail(preferences: Any) -> RemotePolicy:
    """Traduit `workPreferences` en politique de teletravail."""
    if not isinstance(preferences, list):
        return RemotePolicy.UNKNOWN
    trouvees = {_RYTHMES[str(p).upper()] for p in preferences
                if str(p).upper() in _RYTHMES}
    for politique in _ORDRE:
        if politique in trouvees:
            return politique
    return RemotePolicy.UNKNOWN


def _tjm(mission: dict[str, Any]) -> tuple[int | None, int | None]:
    """Lit le budget annonce.

    `budgetBrief` est un champ libre : tantot un nombre nu ("700"), tantot une
    phrase ("TJM HT max 454 € hors frais"). L'extracteur de TJM du pipeline
    couvre les deux ; un nombre nu, lui, n'a pas de contexte, on le lit
    directement.
    """
    brut = str(mission.get("budgetBrief") or "").strip()
    if not brut:
        return None, None
    if brut.isdigit():
        valeur = int(brut)
        return (valeur, valeur) if 100 <= valeur <= 2500 else (None, None)
    return parse_daily_rate(brut)


@register
class CollectiveWorkScraper(BaseScraper):
    name = "collectivework"
    label = "Collective.work (missions freelance)"
    homepage = BASE

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        max_pages = int(self._cfg("max_pages", 5))
        vus: set[str] = set()

        for page in range(1, max_pages + 1):
            params = {"page": page} if page > 1 else None
            try:
                html = self.get(LISTING, params=params)
            except Exception as exc:
                log.warning("Collective.work : page %s indisponible (%s)", page, exc)
                break

            missions = extraire_missions(html)
            if not missions:
                log.info("Collective.work : page %s sans mission, arret.", page)
                break

            for mission in missions:
                offre = self._vers_offre(mission)
                if offre is None or offre.source_id in vus:
                    continue
                vus.add(offre.source_id)
                yield offre

    def _vers_offre(self, mission: dict[str, Any]) -> JobOffer | None:
        titre = str(mission.get("name") or "").strip()
        if not titre:
            return None

        slug = str(mission.get("slug") or "").strip()
        description = strip_html(str(mission.get("description") or ""))
        # Les competences sont deja listees par la plateforme : les joindre au
        # texte evite que l'enrichissement les rate quand le corps est evasif.
        etiquettes = [str(t) for t in (mission.get("projectTypes") or [])]
        etiquettes += [str(t) for t in (mission.get("projectTypeSuggestions") or [])]

        lieu = mission.get("location") or {}
        entreprise = mission.get("company") or {}
        lo, hi = _tjm(mission)

        return JobOffer(
            source=self.name,
            source_id=str(mission.get("id") or slug)[:120],
            url=f"{LISTING}/{slug}" if slug else LISTING,
            title=titre,
            company=str(entreprise.get("name") or ""),
            description=description,
            location=str(lieu.get("fullNameFrench") or lieu.get("fullNameEnglish") or ""),
            remote=teletravail(mission.get("workPreferences")),
            # La plateforme est une place de marche freelance : un CDI y est
            # l'exception, et il se declare.
            contract=(ContractType.CDI if mission.get("isPermanentContract")
                      else ContractType.FREELANCE),
            daily_rate_min=lo,
            daily_rate_max=hi,
            skills=etiquettes,
            published_at=parse_datetime(mission.get("publishedAt")),
        )
