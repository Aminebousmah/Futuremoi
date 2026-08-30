"""Free-Work : missions freelance IT en France (source la plus pertinente ici).

Strategie en deux temps :
  1. la page de resultats fournit les URLs des annonces ;
  2. chaque annonce est lue via son bloc JSON-LD `JobPosting` (stable, expose
     pour Google for Jobs), avec repli sur les selecteurs HTML si absent.

robots.txt n'interdit que /login, /logout et /fw-deals : les pages d'offres sont
accessibles. Le client HTTP applique malgre tout un delai entre requetes.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import ContractType, JobOffer
from ..pipeline.normalize import parse_daily_rate, strip_html
from .base import BaseScraper, register
from .jsonld import find_job_posting, offer_from_jsonld

log = logging.getLogger(__name__)

BASE = "https://www.free-work.com"
SEARCH_URL = f"{BASE}/fr/tech-it/jobs"

_JOB_HREF = re.compile(r"^/fr/tech-it/job-mission/[^\"'#?]+$")


@register
class FreeWorkScraper(BaseScraper):
    name = "freework"
    label = "Free-Work (missions freelance FR)"
    homepage = BASE

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        max_pages = int(self._cfg("max_pages", 2))
        max_offers = int(self._cfg("max_offers", 60))
        queries = self.queries()

        seen: set[str] = set()
        collected = 0
        for query in queries:
            for page in range(1, max_pages + 1):
                links = self._listing_links(query, page)
                if not links:
                    break
                for link in links:
                    if link in seen or collected >= max_offers:
                        continue
                    seen.add(link)
                    offer = self._fetch_detail(link)
                    if offer:
                        collected += 1
                        yield offer
                if collected >= max_offers:
                    return

    def _listing_links(self, query: str, page: int) -> list[str]:
        params = {
            "query": query,
            "contracts": "contractor",   # = freelance chez Free-Work
            "page": page,
        }
        try:
            html_text = self.get(SEARCH_URL, params=params)
        except Exception as exc:
            log.warning("Free-Work : page %s indisponible (%s)", page, exc)
            return []

        soup = BeautifulSoup(html_text, "lxml")
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].split("?")[0]
            if _JOB_HREF.match(href):
                full = urljoin(BASE, href)
                if full not in links:
                    links.append(full)
        return links

    def _fetch_detail(self, url: str) -> JobOffer | None:
        try:
            html_text = self.get(url)
        except Exception as exc:
            log.debug("Free-Work : detail illisible %s (%s)", url, exc)
            return None

        node = find_job_posting(html_text)
        offer = (offer_from_jsonld(node, source=self.name, url=url) if node
                 else self._parse_html(html_text, url))
        if offer is None or not offer.title:
            return None

        # Free-Work affiche le TJM hors du JSON-LD : on le relit dans la page.
        if offer.daily_rate_min is None:
            page_text = strip_html(html_text)
            offer.daily_rate_min, offer.daily_rate_max = parse_daily_rate(page_text)

        # La recherche filtre deja sur "contractor" : on l'assume si non detecte.
        if offer.contract == ContractType.UNKNOWN:
            offer.contract = ContractType.FREELANCE
        return offer

    def _parse_html(self, html_text: str, url: str) -> JobOffer | None:
        """Repli si le JSON-LD disparait : selecteurs larges, volontairement tolerants."""
        soup = BeautifulSoup(html_text, "lxml")
        h1 = soup.find("h1")
        if not h1:
            return None
        company_node = soup.select_one('[class*="company"] a, [class*="company"]')
        main = soup.find("main") or soup.body
        return JobOffer(
            source=self.name,
            url=url,
            title=h1.get_text(" ", strip=True),
            company=company_node.get_text(" ", strip=True) if company_node else "",
            description=strip_html(str(main)) if main else "",
            contract=ContractType.FREELANCE,
        )
