"""Freelance-Informatique : missions freelance IT en France.

Particularite exploitee ici : le site publie l'integralite des annonces d'une
page de resultats dans son JSON-LD (`ItemList` -> `ListItem` -> `JobPosting`).
Une seule requete rend donc ~36 offres completes, description comprise — la
source la plus econome du projet, et la plus polie pour le site.

La recherche par mots-cles se fait en POST ; on s'en passe et on pagine la liste
complete, le filtrage etant de toute facon fait en aval par le pipeline.

robots.txt n'interdit que /forum/, /fr/freelance/, /fr/entreprises/ et /fr/admin/.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from ..models import ContractType, JobOffer
from ..pipeline.normalize import parse_daily_rate
from .base import BaseScraper, register
from .jsonld import find_all_job_postings, offer_from_jsonld

log = logging.getLogger(__name__)

BASE = "https://www.freelance-informatique.fr"
LISTING_URL = f"{BASE}/offres-freelance"


@register
class FreelanceInfoScraper(BaseScraper):
    name = "freelanceinfo"
    label = "Freelance-Informatique (missions FR)"
    homepage = BASE

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        max_pages = int(self._cfg("max_pages", 4))
        vues: set[str] = set()

        for page in range(1, max_pages + 1):
            params = {"page": page} if page > 1 else None
            try:
                html_text = self.get(LISTING_URL, params=params)
            except Exception as exc:
                log.warning("Freelance-Informatique : page %s indisponible (%s)", page, exc)
                break

            noeuds = find_all_job_postings(html_text)
            if not noeuds:
                log.info("Freelance-Informatique : page %s sans offre, arret.", page)
                break

            for noeud in noeuds:
                offre = offer_from_jsonld(noeud, source=self.name, url=BASE)
                if not offre.title or offre.url in vues:
                    continue
                vues.add(offre.url)

                # Le site n'expose pas de baseSalary : le TJM, quand il est
                # annonce, se trouve dans le corps de la description.
                if offre.daily_rate_min is None:
                    offre.daily_rate_min, offre.daily_rate_max = parse_daily_rate(
                        offre.description
                    )
                # Toute la rubrique est freelance par construction.
                if offre.contract == ContractType.UNKNOWN:
                    offre.contract = ContractType.FREELANCE

                # `hiringOrganization` porte le nom du site, pas celui du client :
                # mieux vaut ne rien afficher qu'une entreprise fausse.
                if "freelance-informat" in offre.company.lower().replace(" ", ""):
                    offre.company = ""

                yield offre
