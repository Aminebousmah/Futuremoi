"""Generation de candidature (moteur templates, sans reseau)."""

from __future__ import annotations

from pathlib import Path

from conftest import make_offer

from freelance_radar.apply import ApplicationGenerator
from freelance_radar.models import ApplicationStatus
from freelance_radar.pipeline.score import score_offer


class TestGeneration:
    def test_produit_les_quatre_documents(self, cfg, profile):
        offre = score_offer(make_offer(), profile, cfg)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        dossier = Path(app.file_path)
        for nom in ("lettre.md", "email.md", "offre.md", "checklist.md", "offre.json"):
            assert (dossier / nom).exists(), nom

    def test_statut_brouillon_jamais_envoye(self, cfg, profile):
        # Garde-fou : la generation ne doit jamais marquer une candidature envoyee.
        app = ApplicationGenerator(cfg, profile).generate(make_offer(), force_template=True)
        assert app.status == ApplicationStatus.DRAFTED
        assert app.sent_at is None

    def test_lettre_contient_identite_et_mission(self, cfg, profile):
        offre = score_offer(make_offer(), profile, cfg)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        for attendu in ("Ada Test", "ada@example.com", offre.title, offre.url):
            assert attendu in app.cover_letter

    def test_tjm_propose_borne_par_le_plancher_du_profil(self, cfg, profile):
        # Offre a 450 : on ne descend pas sous le plancher declare (500).
        offre = make_offer(daily_rate_min=450, daily_rate_max=450)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert app.proposed_rate == 500

    def test_align_reprend_le_tjm_annonce_quand_il_depasse_l_objectif(self, cfg, profile):
        # Le client affiche 900 et vise 650 : proposer 650 laisse 250 EUR/j
        # sur la table sans rendre la candidature plus competitive.
        profile.constraints["rate_strategy"] = "align"
        offre = make_offer(daily_rate_min=900, daily_rate_max=900)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert app.proposed_rate == 900

    def test_align_ne_surenchere_pas_sous_l_objectif(self, cfg, profile):
        profile.constraints["rate_strategy"] = "align"
        offre = make_offer(daily_rate_min=550, daily_rate_max=550)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert app.proposed_rate == 550

    def test_tjm_propose_plafonne_a_l_objectif(self, cfg, profile):
        profile.constraints["rate_strategy"] = "target"
        offre = make_offer(daily_rate_min=900, daily_rate_max=900)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert app.proposed_rate == 650

    def test_sans_tjm_affiche_on_propose_l_objectif(self, cfg, profile):
        offre = make_offer(daily_rate_min=None, daily_rate_max=None)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert app.proposed_rate == 650

    def test_checklist_signale_un_cv_manquant(self, cfg, profile):
        app = ApplicationGenerator(cfg, profile).generate(make_offer(), force_template=True)
        checklist = (Path(app.file_path) / "checklist.md").read_text(encoding="utf-8")
        assert "ABSENT" in checklist  # assets/CV.pdf n'existe pas dans les fixtures
        assert "envoi est manuel" in checklist

    def test_pas_de_clause_teletravail_bancale_quand_le_rythme_est_inconnu(self, cfg, profile):
        # Sans le garde-fou, l'email annonce "en a preciser", ce qui ne veut rien dire.
        from freelance_radar.models import RemotePolicy
        offre = make_offer(remote=RemotePolicy.UNKNOWN,
                           description="Mission data sans precision de rythme.")
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert "préciser" not in app.email_body
        assert "€ HT/jour." in app.email_body

    def test_objet_ecrit_une_seule_fois_dans_l_email(self, cfg, profile):
        app = ApplicationGenerator(cfg, profile).generate(make_offer(), force_template=True)
        contenu = (Path(app.file_path) / "email.md").read_text(encoding="utf-8")
        assert contenu.count("Objet :") == 1

    def test_les_ecarts_sont_reportes(self, cfg, profile):
        offre = score_offer(make_offer(skills=["Python", "Kafka"]), profile, cfg)
        app = ApplicationGenerator(cfg, profile).generate(offre, force_template=True)
        assert "Kafka" in app.gaps
