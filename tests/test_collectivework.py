"""Collective.work : lecture des missions embarquees dans la page."""

from __future__ import annotations

import json

from freelance_radar.models import ContractType, RemotePolicy
from freelance_radar.scrapers.collectivework import (
    CollectiveWorkScraper,
    _tjm,
    extraire_missions,
    teletravail,
)

MISSION = {
    "id": "cmthms7e10n0f4keh9civ1ktt",
    "slug": "senior-data-analyst-talend-mmak",
    "name": "Senior Data Analyst Talend",
    "description": "<h3>Contexte</h3><p>Recherche d'un data analyst <b>Talend</b>.</p>",
    "workPreferences": ["HYBRID"],
    "isPermanentContract": False,
    "budgetBrief": None,
    "projectTypes": ["SQL_QUERIES", "ETL", "TALEND"],
    "projectTypeSuggestions": ["Data modeling"],
    "publishedAt": "2026-08-31T19:28:10.381Z",
    "company": {"name": "Anderson RH"},
    "location": {"fullNameFrench": "79000 Niort, France"},
}


def _page(missions) -> str:
    donnees = {"props": {"pageProps": {"dehydratedState": {"queries": [
        {"state": {"data": {"results": {"projects": missions,
                                        "pagination": {"from": 0, "total": 6360}}}}}
    ]}}}}
    return ('<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(donnees) + "</script></html>")


class TestExtraction:
    def test_lit_les_missions(self):
        assert len(extraire_missions(_page([MISSION, MISSION]))) == 2

    def test_page_sans_donnees(self):
        assert extraire_missions("<html><body>rien</body></html>") == []

    def test_structure_inattendue_ne_leve_pas(self):
        """Une source qui change de forme ne doit pas casser la campagne."""
        html = ('<html><script id="__NEXT_DATA__" type="application/json">'
                '{"props": {}}</script></html>')
        assert extraire_missions(html) == []

    def test_json_invalide_ne_leve_pas(self):
        html = ('<html><script id="__NEXT_DATA__" type="application/json">'
                "{pas du json}</script></html>")
        assert extraire_missions(html) == []


class TestTeletravail:
    """La plateforme declare le rythme : plus besoin de le deviner dans le texte."""

    def test_hybride(self):
        assert teletravail(["HYBRID"]) is RemotePolicy.HYBRID

    def test_sur_site(self):
        assert teletravail(["ON_SITE"]) is RemotePolicy.ONSITE

    def test_le_plus_favorable_gagne(self):
        """Une mission ouverte a plusieurs rythmes : c'est ce qui se negocie."""
        assert teletravail(["ON_SITE", "HYBRID"]) is RemotePolicy.HYBRID
        assert teletravail(["HYBRID", "REMOTE"]) is RemotePolicy.FULL_REMOTE

    def test_absent_ou_inconnu(self):
        assert teletravail(None) is RemotePolicy.UNKNOWN
        assert teletravail([]) is RemotePolicy.UNKNOWN
        assert teletravail(["TELEPORTATION"]) is RemotePolicy.UNKNOWN


class TestBudget:
    """`budgetBrief` est un champ libre : nombre nu ou phrase."""

    def test_nombre_nu(self):
        assert _tjm({"budgetBrief": "700"}) == (700, 700)

    def test_phrase(self):
        assert _tjm({"budgetBrief": "TJM HT max 454 € (hors frais)"}) == (454, 454)

    def test_absent(self):
        assert _tjm({"budgetBrief": None}) == (None, None)

    def test_nombre_implausible_ecarte(self):
        assert _tjm({"budgetBrief": "45000"}) == (None, None)


class TestConversion:
    def _offre(self, **surcharge):
        scraper = CollectiveWorkScraper.__new__(CollectiveWorkScraper)
        return scraper._vers_offre({**MISSION, **surcharge})

    def test_champs_principaux(self):
        o = self._offre()
        assert o.title == "Senior Data Analyst Talend"
        assert o.company == "Anderson RH"
        assert o.location == "79000 Niort, France"
        assert o.remote is RemotePolicy.HYBRID
        assert o.contract is ContractType.FREELANCE
        assert o.published_at is not None

    def test_le_html_est_nettoye(self):
        o = self._offre()
        assert "<h3>" not in o.description
        assert "Contexte" in o.description and "Talend" in o.description

    def test_les_etiquettes_rejoignent_les_competences(self):
        """La plateforme liste deja la stack : autant s'en servir."""
        assert "TALEND" in self._offre().skills

    def test_url_construite_sur_le_slug(self):
        assert self._offre().url.endswith("/senior-data-analyst-talend-mmak")

    def test_un_cdi_declare_est_respecte(self):
        assert self._offre(isPermanentContract=True).contract is ContractType.CDI

    def test_mission_sans_titre_ignoree(self):
        assert self._offre(name="") is None

    def test_description_non_tronquee(self):
        """C'est la raison d'etre de cette source face a Adzuna."""
        assert self._offre().description_tronquee is False
