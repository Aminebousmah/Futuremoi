"""France Travail : conversion et garde-fous de l'API v2 (aucun appel reseau)."""

from __future__ import annotations

import pytest

from freelance_radar.models import ContractType
from freelance_radar.scrapers.francetravail import (
    PUBLIEE_DEPUIS_ALLOWED,
    FranceTravailScraper,
    snap_publiee_depuis,
)

OFFRE = {
    "id": "191QBCD",
    "intitule": "Data Engineer (H/F)",
    "description": "Mission de conception de pipelines de donnees.",
    "dateCreation": "2026-08-25T09:12:00.000Z",
    "typeContrat": "LIB",
    "typeContratLibelle": "Profession liberale",
    "experienceLibelle": "5 ans",
    "lieuTravail": {"libelle": "31 - TOULOUSE", "codePostal": "31000"},
    "entreprise": {"nom": "ACME Conseil"},
    "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/191QBCD"},
    "competences": [{"libelle": "SQL"}, {"libelle": "Python"}, {"libelle": ""}],
}


@pytest.fixture
def scraper(cfg):
    # Le client HTTP n'est pas sollicite : on ne teste que la conversion.
    return FranceTravailScraper(cfg, client=None, source_cfg={})


class TestPublieeDepuis:
    @pytest.mark.parametrize("demande,attendu", [
        (1, 1), (2, 3), (3, 3), (7, 7), (10, 14), (30, 31), (31, 31),
    ])
    def test_arrondit_vers_la_valeur_autorisee_superieure(self, demande, attendu):
        # Arrondir vers le bas retrecirait la fenetre demandee sans le dire.
        assert snap_publiee_depuis(demande) == attendu

    @pytest.mark.parametrize("demande", [0, None, 90, 365])
    def test_hors_bornes_plafonne_au_maximum(self, demande):
        assert snap_publiee_depuis(demande) == 31

    def test_le_resultat_est_toujours_une_valeur_acceptee(self):
        # L'API rejette en 400 toute autre valeur : c'est l'invariant a tenir.
        assert all(snap_publiee_depuis(n) in PUBLIEE_DEPUIS_ALLOWED for n in range(0, 60))


class TestConversion:
    def test_champs_principaux(self, scraper):
        offre = scraper._parse(OFFRE)
        assert offre.title == "Data Engineer (H/F)"
        assert offre.company == "ACME Conseil"
        assert offre.location == "31 - TOULOUSE"
        assert offre.source_id == "191QBCD"
        assert offre.published_at.year == 2026

    def test_profession_liberale_est_du_freelance(self, scraper):
        assert scraper._parse(OFFRE).contract == ContractType.FREELANCE

    def test_interim_n_est_pas_du_freelance(self, scraper):
        # MIS = mission d'interim : du salariat temporaire, hors perimetre.
        offre = scraper._parse({**OFFRE, "typeContrat": "MIS"})
        assert offre.contract == ContractType.CDD

    def test_alternance_detectee(self, scraper):
        offre = scraper._parse({**OFFRE, "typeContrat": "CDD", "alternance": True})
        assert offre.contract == ContractType.ALTERNANCE

    def test_code_contrat_inconnu_reste_indetermine(self, scraper):
        offre = scraper._parse({**OFFRE, "typeContrat": "ZZZ"})
        assert offre.contract == ContractType.UNKNOWN

    def test_les_libelles_sont_annexes_a_la_description(self, scraper):
        # Sans cela, le pipeline (qui ne lit que du texte) perd ces indices.
        offre = scraper._parse(OFFRE)
        assert "Profession liberale" in offre.description
        assert offre.description.startswith("Mission de conception")

    def test_competences_vides_ecartees(self, scraper):
        assert scraper._parse(OFFRE).skills == ["SQL", "Python"]

    def test_url_reconstruite_si_absente(self, scraper):
        offre = scraper._parse({**OFFRE, "origineOffre": {}})
        assert offre.url.endswith("/191QBCD")

    def test_offre_sans_intitule_ignoree(self, scraper):
        assert scraper._parse({"id": "x"}) is None


class ClientEspion:
    """Client HTTP factice : enregistre les parametres, ne rend aucune offre."""

    def __init__(self):
        self.appels: list[dict] = []

    def post_form(self, url, data, headers=None):
        return {"access_token": "jeton-de-test"}

    def get(self, url, params=None, headers=None, **kwargs):
        self.appels.append(dict(params or {}))
        return '{"resultats": []}'


class TestConstructionDesRequetes:
    def test_un_appel_par_mot_cle(self, cfg):
        """`motsCles` combine les termes par un ET cote API.

        Envoyer "data,donnees" ne rend aucune offre : les deux mots devraient
        alors figurer dans la meme annonce. Il faut donc une requete par terme.
        """
        espion = ClientEspion()
        scraper = FranceTravailScraper(
            cfg, client=espion, source_cfg={"queries": ["data", "donnees"]}
        )
        scraper.client = espion
        list(scraper.fetch(["data"]))

        termes = [a.get("motsCles") for a in espion.appels]
        assert termes == ["data", "donnees"]
        assert not any("," in str(t) for t in termes)

    def test_anciennete_toujours_dans_les_valeurs_autorisees(self, cfg):
        espion = ClientEspion()
        cfg.filters.max_age_days = 30          # valeur refusee telle quelle par l'API
        scraper = FranceTravailScraper(cfg, client=espion, source_cfg={"queries": ["data"]})
        list(scraper.fetch(["data"]))
        assert espion.appels[0]["publieeDepuis"] in PUBLIEE_DEPUIS_ALLOWED
