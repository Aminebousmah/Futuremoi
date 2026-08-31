"""Mindquest (ex-Club Freelance) : missions freelance IT et finance.

La source ne propose pas de recherche exploitable sans JavaScript, mais elle
publie un sitemap dedie aux missions -- `sitemap-missions.xml` -- et chaque
fiche porte un `JobPosting` en JSON-LD complet. On lit donc le sitemap, puis
les fiches : deterministe, sans rendu de page, et poli pour le site.

robots.txt (verifie le 31/08/2026) autorise tout sauf `/api/`, les tunnels
`signup-*` et `/dashboard/`. Les fiches de mission n'y sont pas.

QUATRE PIEGES, tous mesures sur la source le 31/08/2026 :

1. **Le slug porte l'intitule du poste.** Le filtrer avant de charger les
   fiches change tout : sur 128 missions, une douzaine seulement sont data.
   Sans ce tri, 60 pages etaient telechargees pour 5 offres pertinentes.
2. **`datePosted` est la date de creation, pas de rafraichissement.** Des
   missions toujours listees affichent 2024, et `max_age_days` les rejetait
   toutes. Le `<lastmod>` du sitemap est le bon signal : il se repartit sur
   mars a aout 2026, donc il suit l'activite reelle des annonces.
3. **`baseSalary` est faux.** Une fiche annonce `value: 50000, unitText:
   "DAY"` -- 50 000 EUR par jour, en realite un salaire annuel mal etiquete.
   Le garde-fou de `jsonld._salary` l'ecarte deja ; le TJM reel se lit dans
   la description.
4. **`employmentType` vaut FULL_TIME** sur une place de marche freelance. Le
   contrat est donc force, jamais deduit de ce champ.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

from ..models import ContractType, JobOffer
from ..pipeline.normalize import contains_any, parse_daily_rate, parse_datetime
from .base import BaseScraper, register
from .jsonld import find_job_posting, offer_from_jsonld

log = logging.getLogger(__name__)

BASE = "https://mindquest.io"
SITEMAP_URL = f"{BASE}/fr/sitemap-missions.xml"

# Une fiche de mission porte son identifiant numerique dans l'URL :
# /fr/missions-freelance-offres-emploi-it-finance/81500/2-developpeurs-java-hf-13
# Les URL sans ce segment sont des pages de liste ou des filtres a categorie.
_URL_MISSION = re.compile(r"/missions-[^/]+/\d+/[^/?#]+$")
_ENTREE_RE = re.compile(r"<url>(.*?)</url>", re.IGNORECASE | re.DOTALL)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.IGNORECASE)


def _intitule(url: str) -> str:
    """Rend le slug de fin d'URL sous forme lisible.

    ".../81500/data-engineer-dataiku-hf-79" -> "data engineer dataiku hf 79".
    C'est l'intitule du poste, disponible sans charger la page.
    """
    return url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")


def lire_sitemap(xml: str) -> list[tuple[str, str]]:
    """Rend les couples (url de mission, lastmod) presents dans le sitemap."""
    entrees: list[tuple[str, str]] = []
    for bloc in _ENTREE_RE.findall(xml):
        loc = _LOC_RE.search(bloc)
        if not loc or not _URL_MISSION.search(loc.group(1)):
            continue
        lastmod = _LASTMOD_RE.search(bloc)
        entrees.append((loc.group(1), lastmod.group(1) if lastmod else ""))
    return entrees


@register
class MindquestScraper(BaseScraper):
    name = "mindquest"
    label = "Mindquest (missions freelance IT/finance)"
    homepage = BASE

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        max_missions = int(self._cfg("max_missions", 40))

        try:
            sitemap = self.get(SITEMAP_URL)
        except Exception as exc:
            log.warning("Mindquest : sitemap indisponible (%s)", exc)
            return

        entrees = lire_sitemap(sitemap)
        if not entrees:
            log.warning("Mindquest : aucune mission dans le sitemap.")
            return

        # Tri sur le slug d'abord : c'est ce qui evite de telecharger 90 % de
        # fiches hors sujet. Puis les plus recemment mises a jour en tete.
        retenues = [e for e in entrees
                    if contains_any(_intitule(e[0]), self.cfg.search.keywords_any)]
        retenues.sort(key=lambda e: e[1], reverse=True)
        candidates = len(retenues)
        retenues = retenues[:max_missions]

        log.info("Mindquest : %d missions au sitemap, %d data, %d fiches lues.",
                 len(entrees), candidates, len(retenues))

        for url, lastmod in retenues:
            try:
                html_text = self.get(url)
            except Exception as exc:
                log.debug("Mindquest : fiche ignoree (%s) %s", exc, url)
                continue

            noeud = find_job_posting(html_text)
            if noeud is None:
                continue

            offre = offer_from_jsonld(noeud, source=self.name, url=url)
            if not offre.title:
                continue

            # `lastmod` prime sur `datePosted` : c'est la seule date qui dit si
            # l'annonce est encore vivante (cf. piege 2 de l'en-tete).
            maj = parse_datetime(lastmod)
            if maj is not None:
                offre.published_at = maj

            # `baseSalary` n'est pas fiable ici : le TJM se lit dans le corps.
            if offre.daily_rate_min is None:
                offre.daily_rate_min, offre.daily_rate_max = parse_daily_rate(
                    offre.description
                )
            # Place de marche freelance : le contrat ne se deduit pas du JSON-LD.
            offre.contract = ContractType.FREELANCE
            # `hiringOrganization` porte "Mindquest", pas le client final :
            # mieux vaut ne rien afficher qu'une entreprise fausse.
            if "mindquest" in offre.company.lower():
                offre.company = ""

            yield offre
