"""Generation du CV en PDF."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_offer

from freelance_radar.apply.pdf import (
    PolicesIntrouvables,
    decouper_gras,
    echapper_markdown,
    generer_cv,
)


# --------------------------------------------------------------------------- #
#  Balisage du gras
# --------------------------------------------------------------------------- #
class TestDecoupageGras:
    def test_texte_simple(self):
        blocs = decouper_gras("Pilotage du budget")
        assert len(blocs) == 1
        assert blocs[0].texte == "Pilotage du budget" and not blocs[0].gras

    def test_gras_au_milieu(self):
        blocs = decouper_gras("Pilotage du **processus budgétaire** annuel")
        assert [(b.texte, b.gras) for b in blocs] == [
            ("Pilotage du ", False),
            ("processus budgétaire", True),
            (" annuel", False),
        ]

    def test_plusieurs_segments(self):
        blocs = decouper_gras("**SAP BI4** puis **Power BI**")
        assert [b.gras for b in blocs] == [True, False, True]

    def test_gras_en_tete(self):
        blocs = decouper_gras("**Conception** et deploiement")
        assert blocs[0].gras is True

    def test_texte_vide(self):
        assert decouper_gras("")[0].texte == ""


class TestEchappement:
    """fpdf2 lit `__` et `--` comme du style : il faut les neutraliser."""

    def test_souligne_echappe(self):
        assert "\\_\\_" in echapper_markdown("champ __interne__")

    def test_double_tiret_echappe(self):
        assert "\\-\\-" in echapper_markdown("option --verbose")

    def test_texte_courant_intact(self):
        texte = "Excel (Power Query, TCD, VBA) · SAP BusinessObjects (BI4)"
        assert echapper_markdown(texte) == texte


# --------------------------------------------------------------------------- #
#  Generation
# --------------------------------------------------------------------------- #
@pytest.fixture
def offre():
    return make_offer(title="Consultant Power BI",
                      description="Mission BI : Power BI, SQL, modélisation.")


class TestGeneration:
    def _pdf(self, tmp_path, offre, profile):
        try:
            return generer_cv(offre, profile, tmp_path / "cv.pdf")
        except PolicesIntrouvables:
            pytest.skip("aucune police système compatible sur cette machine")

    def test_produit_un_pdf_valide(self, tmp_path, offre, profile):
        chemin = self._pdf(tmp_path, offre, profile)
        assert chemin.exists()
        assert chemin.read_bytes().startswith(b"%PDF-")

    def test_cree_le_dossier_manquant(self, tmp_path, offre, profile):
        cible = tmp_path / "creuse" / "encore" / "cv.pdf"
        try:
            generer_cv(offre, profile, cible)
        except PolicesIntrouvables:
            pytest.skip("aucune police système compatible")
        assert cible.exists()

    def test_photo_absente_ne_casse_pas(self, tmp_path, offre, profile):
        """Un profil sans photo doit tout de meme rendre un CV."""
        profile.documents = dict(profile.documents or {})
        profile.documents.pop("photo", None)
        chemin = self._pdf(tmp_path, offre, profile)
        assert chemin.exists()

    def test_photo_introuvable_ne_casse_pas(self, tmp_path, offre, profile):
        profile.documents = {**(profile.documents or {}), "photo": "nulle/part.png"}
        chemin = self._pdf(tmp_path, offre, profile)
        assert chemin.exists()

    def test_parcours_vide_ne_casse_pas(self, tmp_path, offre, profile):
        """Un profil sans experiences declarees rend un CV plus court."""
        profile.cv = {**profile.cv, "parcours": {}}
        chemin = self._pdf(tmp_path, offre, profile)
        assert chemin.exists()

    def test_le_pdf_change_avec_l_offre(self, tmp_path, profile):
        """Le CV est adapte : deux offres differentes ne rendent pas le meme."""
        bi = make_offer(title="Consultant Power BI",
                        description="Power BI, DAX, tableaux de bord.")
        ml = make_offer(title="Data Scientist",
                        description="Scikit-learn, XGBoost, séries temporelles.")
        try:
            a = generer_cv(bi, profile, tmp_path / "a.pdf").read_bytes()
            b = generer_cv(ml, profile, tmp_path / "b.pdf").read_bytes()
        except PolicesIntrouvables:
            pytest.skip("aucune police système compatible")
        assert a != b


class TestIntegrationGenerateur:
    """Le PDF doit rejoindre le dossier de candidature, sans le bloquer."""

    def test_apply_produit_le_pdf(self, cfg, profile):
        from freelance_radar.apply import ApplicationGenerator
        from freelance_radar.pipeline.score import score_offer

        offre = score_offer(make_offer(), profile, cfg)
        candidature = ApplicationGenerator(cfg, profile).generate(offre)
        dossier = Path(candidature.file_path)
        if not (dossier / "cv.pdf").exists():
            pytest.skip("aucune police système compatible sur cette machine")
        assert (dossier / "cv.pdf").read_bytes().startswith(b"%PDF-")

    def test_un_echec_pdf_ne_bloque_pas_le_dossier(self, cfg, profile, monkeypatch):
        """Une police manquante ne doit pas emporter la lettre et l'email."""
        from freelance_radar.apply import ApplicationGenerator, generator
        from freelance_radar.pipeline.score import score_offer

        def casse(*_a, **_k):
            raise PolicesIntrouvables("simulation")

        monkeypatch.setattr("freelance_radar.apply.pdf.generer_cv", casse)
        offre = score_offer(make_offer(), profile, cfg)
        candidature = ApplicationGenerator(cfg, profile).generate(offre)
        assert (Path(candidature.file_path) / "lettre.md").exists()
        assert (Path(candidature.file_path) / "cv-adapte.md").exists()
        assert generator  # le module reste importable
