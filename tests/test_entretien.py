"""Fiche de preparation d'entretien."""

from __future__ import annotations

import pytest
from conftest import make_offer

from freelance_radar.apply.entretien import (
    ADJACENCES,
    QUESTIONS_PAR_COMPETENCE,
    REVISIONS,
    adjacents,
    construire_fiche,
    rendre_markdown,
    sujets_cles,
)


# --------------------------------------------------------------------------- #
#  Coherence des tables
# --------------------------------------------------------------------------- #
class TestCoherenceDesCles:
    """Une cle mal orthographiee ne declenche jamais, et sans erreur visible.

    Le piege est reel : les intitules de l'inventaire portent des accents
    ("Modele en etoile" ne matche pas "Modèle en étoile"), et une entree
    inerte passerait inapercue jusqu'a un entretien rate.
    """

    def test_toutes_les_cles_existent_dans_l_inventaire_reel(self):
        """Le seul test qui attrape vraiment une faute d'accent.

        Il lit `config/profile.yaml`, le vrai inventaire, et se passe quand ce
        fichier personnel est absent (il est hors depot). Une premiere version
        de REVISIONS ecrivait "Modele en etoile (Kimball)" sans accents :
        l'entree existait, ne se declenchait jamais, et rien ne le signalait.
        """
        from freelance_radar.config import load_profile, project_root

        if not (project_root() / "config" / "profile.yaml").exists():
            pytest.skip("profile.yaml absent (fichier personnel, hors depot)")

        inventaire = {c for outils
                      in (load_profile().cv or {}).get("competences", {}).values()
                      for c in outils}
        assert inventaire, "inventaire vide : le test ne prouverait rien"

        tables = {
            "REVISIONS": set(REVISIONS),
            "QUESTIONS_PAR_COMPETENCE": set(QUESTIONS_PAR_COMPETENCE),
            "ADJACENCES (clés)": set(ADJACENCES),
            "ADJACENCES (cibles)": {c for v in ADJACENCES.values() for c in v},
        }
        for nom, cles in tables.items():
            inconnues = sorted(cles - inventaire)
            assert not inconnues, f"{nom} : intitulés absents de l'inventaire {inconnues}"

    def test_adjacences_pointent_vers_des_sujets_revisables(self):
        """Un sujet adjacent sans fiche de revision n'apporte rien."""
        for source, cibles in ADJACENCES.items():
            for cible in cibles:
                assert cible in REVISIONS, f"{source} -> {cible} n'a pas de revision"

    def test_pas_d_adjacence_circulaire_immediate(self):
        for source, cibles in ADJACENCES.items():
            assert source not in cibles, f"{source} se pointe lui-meme"

    def test_questions_portent_sur_des_sujets_revisables(self):
        for sujet in QUESTIONS_PAR_COMPETENCE:
            assert sujet in REVISIONS, f"{sujet} a des questions mais pas de revision"


# --------------------------------------------------------------------------- #
#  Adjacence
# --------------------------------------------------------------------------- #
class TestAdjacence:
    def test_power_bi_implique_dax(self):
        """Une annonce Power BI ne dit pas "DAX" ; l'entretien le demandera."""
        assert "DAX" in adjacents(["Power BI"])

    def test_pas_de_doublon_avec_les_sujets_cites(self):
        implicites = adjacents(["Power BI", "DAX"])
        assert "DAX" not in implicites

    def test_pas_de_doublon_entre_adjacents(self):
        implicites = adjacents(["Snowflake", "BigQuery"])
        assert implicites.count("SQL") == 1

    def test_sujet_sans_adjacence(self):
        assert adjacents(["Docker"]) == []


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #
@pytest.fixture
def offre_bi():
    return make_offer(
        title="Consultant Power BI",
        description=("Mission freelance : refonte du reporting sous Power BI. "
                     "Modelisation, mesures et tableaux de bord pour la direction."),
    )


class TestConstruction:
    def test_sujets_cles_notes_positivement(self, offre_bi, profile):
        sujets = sujets_cles(offre_bi, profile)
        assert sujets, "aucun sujet identifie sur une offre Power BI"
        assert all(score > 0 for _, score in sujets)
        assert sujets == sorted(sujets, key=lambda s: -s[1])

    def test_power_bi_en_tete(self, offre_bi, profile):
        sujets = sujets_cles(offre_bi, profile)
        assert sujets[0][0] == "Power BI"

    def test_fiche_complete(self, offre_bi, profile, cfg):
        fiche = construire_fiche(offre_bi, profile, cfg)
        assert fiche.sujets_cles
        assert fiche.questions_techniques
        assert fiche.revisions
        assert fiche.vigilance

    def test_les_ecarts_passent_en_premier(self, offre_bi, profile, cfg):
        """Ce qui manque au profil se revise avant ce qu'on maitrise deja."""
        offre_bi.score_detail = {"_missing_skills": ["dbt"]}
        fiche = construire_fiche(offre_bi, profile, cfg)
        assert fiche.revisions[0].sujet == "dbt"
        assert "absent" in fiche.revisions[0].raison

    def test_ecart_produit_une_question(self, offre_bi, profile, cfg):
        offre_bi.score_detail = {"_missing_skills": ["Airflow"]}
        fiche = construire_fiche(offre_bi, profile, cfg)
        assert any("Airflow" in q.texte for q in fiche.questions_ecarts)

    def test_aucun_doublon_de_revision(self, offre_bi, profile, cfg):
        offre_bi.score_detail = {"_missing_skills": ["Power BI"]}
        fiche = construire_fiche(offre_bi, profile, cfg)
        sujets = [r.sujet for r in fiche.revisions]
        assert len(sujets) == len(set(sujets))

    def test_vigilance_rappelle_le_tjm(self, offre_bi, profile, cfg):
        fiche = construire_fiche(offre_bi, profile, cfg)
        assert any("TJM" in p for p in fiche.vigilance)
        assert any(str(profile.rate_target) in p for p in fiche.vigilance)

    def test_vigilance_rappelle_qu_aucun_envoi_n_a_eu_lieu(self, offre_bi, profile, cfg):
        fiche = construire_fiche(offre_bi, profile, cfg)
        assert any("envoie rien" in p for p in fiche.vigilance)


# --------------------------------------------------------------------------- #
#  Rendu
# --------------------------------------------------------------------------- #
class TestRendu:
    def test_markdown_structure(self, offre_bi, profile, cfg):
        texte = rendre_markdown(construire_fiche(offre_bi, profile, cfg))
        for section in ("# Préparation d'entretien",
                        "## Ce que l'offre met sur la table",
                        "## Questions techniques probables",
                        "## Points à réviser",
                        "## Questions de posture",
                        "## Questions freelance",
                        "## À poser vous-même",
                        "## Points de vigilance"):
            assert section in texte, f"section manquante : {section}"

    def test_lien_vers_l_annonce(self, offre_bi, profile, cfg):
        texte = rendre_markdown(construire_fiche(offre_bi, profile, cfg))
        assert offre_bi.url in texte

    def test_section_ecarts_absente_si_rien_ne_manque(self, offre_bi, profile, cfg):
        offre_bi.score_detail = {"_missing_skills": []}
        texte = rendre_markdown(construire_fiche(offre_bi, profile, cfg))
        assert "les écarts" not in texte

    def test_section_ecarts_presente_sinon(self, offre_bi, profile, cfg):
        offre_bi.score_detail = {"_missing_skills": ["dbt"]}
        texte = rendre_markdown(construire_fiche(offre_bi, profile, cfg))
        assert "les écarts" in texte

    def test_realisation_multiligne_reste_sur_une_puce(self, offre_bi, profile, cfg):
        """Le profil saisit les realisations sur plusieurs lignes."""
        profile.references[0]["achievement"] = "Refonte\ndu\nDWH."
        profile.references[0]["stack"] = ["Power BI"]
        texte = rendre_markdown(construire_fiche(offre_bi, profile, cfg))
        assert "Refonte du DWH." in texte
