"""Recomposition du parcours : ordre, variantes, epingle, competences imposees."""

from __future__ import annotations

from conftest import make_offer

from freelance_radar.apply.cv import classer_competences
from freelance_radar.apply.parcours import (
    competences_citees,
    composer_experiences,
    composer_projets,
)


def _profil_parcours(profile, **parcours):
    profile.cv = {**profile.cv, "parcours": parcours}
    return profile


class TestDetectionDesOutils:
    def test_reconnait_les_outils_cites(self, profile):
        cites = competences_citees(
            "Conception de **dashboards** (**Power BI**) sur **Snowflake**.", profile)
        assert "Power BI" in cites and "Snowflake" in cites

    def test_puce_sans_outil(self, profile):
        assert competences_citees("Animation de sessions de formation.", profile) == []


class TestOrdreDesPuces:
    def test_la_puce_pertinente_remonte(self, profile):
        _profil_parcours(profile, experiences=[{
            "poste": "Data Analyst", "client": "X",
            "puces": ["Suivi budgétaire mensuel.",
                      "Conception de dashboards **Power BI**."],
        }])
        offre = make_offer(title="Consultant Power BI", description="Power BI, DAX.")
        puces = composer_experiences(offre, profile)[0].puces
        assert "Power BI" in puces[0].texte

    def test_l_ordre_des_postes_ne_bouge_pas(self, profile):
        """Un CV se lit du plus recent au plus ancien."""
        _profil_parcours(profile, experiences=[
            {"poste": "Recent", "client": "A", "puces": ["Rien de pertinent."]},
            {"poste": "Ancien", "client": "B", "puces": ["Dashboards **Power BI**."]},
        ])
        offre = make_offer(title="Consultant Power BI", description="Power BI")
        assert [b.titre for b in composer_experiences(offre, profile)] == \
            ["Recent", "Ancien"]

    def test_les_projets_se_reordonnent(self, profile):
        """Les projets n'ont pas de chronologie : le plus proche passe devant."""
        _profil_parcours(profile, projets=[
            {"nom": "Hors sujet", "sous_titre": "", "puces": ["Rien."]},
            {"nom": "Proche", "sous_titre": "", "puces": ["Modèles **Scikit-learn**."]},
        ])
        offre = make_offer(title="Data Scientist", description="Scikit-learn, ML.")
        assert composer_projets(offre, profile)[0].titre == "Proche"


class TestEpingleDeNature:
    """Un projet dont toutes les puces penchent vers l'offre ne dit plus ce qu'il est."""

    def test_la_puce_de_nature_reste_en_tete(self, profile):
        _profil_parcours(profile, projets=[{
            "nom": "Outil", "sous_titre": "",
            "puces": [
                {"texte": "Développement d'un outil de recrutement.", "nature": True},
                "Modèles **Scikit-learn** et **XGBoost**.",
            ],
        }])
        offre = make_offer(title="Data Scientist", description="Scikit-learn XGBoost")
        puces = composer_projets(offre, profile)[0].puces
        assert "outil de recrutement" in puces[0].texte
        assert puces[0].nature is True
        assert "Scikit-learn" in puces[1].texte


class TestVariantes:
    def test_la_variante_qui_recoupe_l_offre_est_choisie(self, profile):
        _profil_parcours(profile, experiences=[{
            "poste": "BI", "client": "X",
            "puces": [{
                "texte": "Consolidation des données du réseau.",
                "variantes": ["Consolidation des données du réseau sous **Snowflake**."],
            }],
        }])
        offre = make_offer(title="Data Engineer", description="Entrepôt Snowflake.")
        puce = composer_experiences(offre, profile)[0].puces[0]
        assert "Snowflake" in puce.texte
        assert puce.reformulee is True

    def test_la_formulation_d_origine_gagne_a_egalite(self, profile):
        """On ne reecrit pas un CV pour un gain nul."""
        _profil_parcours(profile, experiences=[{
            "poste": "BI", "client": "X",
            "puces": [{"texte": "Formulation d'origine.",
                       "variantes": ["Autre formulation."]}],
        }])
        offre = make_offer(title="Chef de projet", description="Rien de technique.")
        puce = composer_experiences(offre, profile)[0].puces[0]
        assert puce.texte == "Formulation d'origine."
        assert puce.reformulee is False


class TestCompetencesImposees:
    """Vous savez parfois qu'un outil comptera alors que l'annonce ne le nomme pas."""

    def test_une_competence_imposee_passe_devant(self, profile):
        offre = make_offer(title="Data Analyst", description="Reporting mensuel.")
        sans = {c: s for _, c, s in classer_competences(offre, profile)}
        avec = {c: s for _, c, s in classer_competences(offre, profile, ["DAX"])}
        assert avec["DAX"] > sans["DAX"]
        assert max(avec, key=avec.get) == "DAX"

    def test_l_intitule_est_compare_sans_casse_ni_accent(self, profile):
        offre = make_offer(title="Data Analyst", description="Reporting.")
        scores = {c: s for _, c, s in classer_competences(offre, profile, ["power bi"])}
        assert max(scores, key=scores.get) == "Power BI"

    def test_une_competence_inconnue_est_ignoree(self, profile):
        offre = make_offer(title="Data Analyst", description="Reporting.")
        assert classer_competences(offre, profile, ["Cobol des familles"])

    def test_les_imposees_remontent_aussi_les_puces(self, profile):
        _profil_parcours(profile, experiences=[{
            "poste": "BI", "client": "X",
            "puces": ["Suivi budgétaire.", "Mesures **DAX** sur le modèle."],
        }])
        offre = make_offer(title="Data Analyst", description="Reporting mensuel.")
        puces = composer_experiences(offre, profile, ["DAX"])[0].puces
        assert "DAX" in puces[0].texte
