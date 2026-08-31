"""Lettre de motivation generique."""

from __future__ import annotations

from freelance_radar.apply.lettre import (
    _date_lisible,
    ecrire_lettre_generique,
    lettre_generique,
)


class TestDateLisible:
    """Une lettre ne s'ecrit pas en ISO."""

    def test_premier_du_mois(self):
        assert _date_lisible("2026-10-01") == "1er octobre 2026"

    def test_jour_courant(self):
        assert _date_lisible("2026-03-15") == "15 mars 2026"

    def test_valeur_illisible_rendue_telle_quelle(self):
        assert _date_lisible("des que possible") == "des que possible"

    def test_valeur_vide(self):
        assert _date_lisible(None) == ""


class TestContenu:
    def test_reprend_l_identite(self, profile):
        texte = lettre_generique(profile)
        assert profile.identity["full_name"] in texte
        assert profile.identity["email"] in texte

    def test_reprend_le_pitch(self, profile):
        texte = lettre_generique(profile)
        assert profile.positioning["pitch"].split(".")[0] in texte

    def test_cite_les_references(self, profile):
        texte = lettre_generique(profile)
        assert profile.references[0]["client"] in texte

    def test_ne_cite_pas_toutes_les_references(self, profile):
        """Au-dela de deux, la lettre devient un CV en prose."""
        profile.references = [
            {"client": f"Client {i}", "role": "Data", "achievement": "x"}
            for i in range(5)
        ]
        texte = lettre_generique(profile)
        assert "Client 0" in texte and "Client 4" not in texte

    def test_annonce_les_conditions(self, profile):
        texte = lettre_generique(profile)
        assert str(profile.rate_target) in texte
        assert "disponible" in texte

    def test_ne_depend_d_aucune_offre(self, profile):
        """Elle est generique : deux appels rendent le meme texte."""
        assert lettre_generique(profile) == lettre_generique(profile)

    def test_profil_minimal_ne_casse_pas(self, profile):
        profile.references = []
        profile.constraints = {}
        assert lettre_generique(profile).strip()


class TestEcriture:
    def test_ecrit_le_fichier(self, tmp_path, profile):
        chemin = ecrire_lettre_generique(profile, tmp_path / "l.md")
        assert chemin.exists()
        assert "Bonjour," in chemin.read_text(encoding="utf-8")

    def test_cree_le_dossier(self, tmp_path, profile):
        chemin = ecrire_lettre_generique(profile, tmp_path / "a" / "b" / "l.md")
        assert chemin.exists()
