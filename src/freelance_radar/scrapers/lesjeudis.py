"""LesJeudis : job board IT francais (Talent.com / DHI).

Meme strategie que Free-Work : la page de resultats donne les URLs, chaque
annonce expose un `JobPosting` JSON-LD. Le catalogue est majoritairement salarie ;
les missions freelance y sont minoritaires mais bien presentes, et le filtre de
contrat du pipeline fait le tri.

robots.txt n'interdit rien pour les agents generiques.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import JobOffer
from ..pipeline.normalize import parse_daily_rate, strip_html
from .base import BaseScraper, register
from .jsonld import find_job_posting, offer_from_jsonld

log = logging.getLogger(__name__)

BASE = "https://www.lesjeudis.com"
SEARCH_URL = f"{BASE}/jobs"
_JOB_HREF = re.compile(r"^/fr/job/[^\"'#?]+$")


@register
class LesJeudisScraper(BaseScraper):
    name = "lesjeudis"
    label = "LesJeudis (IT France)"
    homepage = BASE

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        max_pages = int(self._cfg("max_pages", 2))
        max_offers = int(self._cfg("max_offers", 40))
        requetes = self._cfg("queries") or ["data"]

        vues: set[str] = set()
        collectees = 0
        for requete in requetes:
            for page in range(1, max_pages + 1):
                for lien in self._liens(requete, page):
                    if lien in vues or collectees >= max_offers:
                        continue
                    vues.add(lien)
                    offre = self._detail(lien)
                    if offre:
                        collectees += 1
                        yield offre
                if collectees >= max_offers:
                    return

    def _liens(self, requete: str, page: int) -> list[str]:
        params = {"q": requete, "l": "France"}
        if page > 1:
            params["p"] = page
        try:
            html_text = self.get(SEARCH_URL, params=params)
        except Exception as exc:
            log.warning("LesJeudis : page %s indisponible (%s)", page, exc)
            return []

        soup = BeautifulSoup(html_text, "lxml")
        liens: list[str] = []
        for ancre in soup.find_all("a", href=True):
            href = ancre["href"].split("?")[0]
            if _JOB_HREF.match(href):
                complet = urljoin(BASE, href)
                if complet not in liens:
                    liens.append(complet)
        return liens

    def _detail(self, url: str) -> JobOffer | None:
        try:
            html_text = self.get(url)
        except Exception as exc:
            log.debug("LesJeudis : detail illisible %s (%s)", url, exc)
            return None

        noeud = find_job_posting(html_text)
        if noeud is None:
            return None
        offre = offer_from_jsonld(noeud, source=self.name, url=url)
        if not offre.title:
            return None
        if offre.daily_rate_min is None:
            offre.daily_rate_min, offre.daily_rate_max = parse_daily_rate(
                strip_html(html_text)
            )
        return offre
