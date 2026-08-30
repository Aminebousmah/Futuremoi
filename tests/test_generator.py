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


class TestAdaptationCV:
    """Adaptation du CV : ordre des rubriques et paragraphe de profil."""

    @staticmethod
    def _offre_bi():
        return make_offer(
            title="Consultant Power BI senior",
            description="Refonte des tableaux de bord décisionnels.",
            skills=["Power BI", "Tableau", "Reporting"],
        )

    def test_la_rubrique_demandee_passe_en_tete(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        mobiles = [r.label for r in a.rubriques if not r.epinglee]
        assert mobiles[0] == "Data Visualisation & BI"

    def test_les_rubriques_epinglees_ne_bougent_pas(self, cfg, profile):
        from freelance_radar.apply.cv import RUBRIQUES_CV, adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        positions = [i for i, r in enumerate(a.rubriques) if r.epinglee]
        attendues = [i for i, r in enumerate(RUBRIQUES_CV) if r.epinglee]
        assert positions == attendues

    def test_aucune_rubrique_perdue_ni_dupliquee(self, cfg, profile):
        from freelance_radar.apply.cv import RUBRIQUES_CV, adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        assert sorted(r.label for r in a.rubriques) == sorted(r.label for r in RUBRIQUES_CV)

    def test_pas_de_fuite_d_etat_entre_deux_offres(self, cfg, profile):
        # RUBRIQUES_CV est partage : sans copie, le score d'une offre
        # contaminerait le classement de la suivante.
        from freelance_radar.apply.cv import adapter_cv

        bi = [r.label for r in adapter_cv(self._offre_bi(), profile).rubriques]
        cloud = make_offer(title="Data Engineer", skills=["AWS", "Docker", "Airflow"])
        adapter_cv(cloud, profile)
        assert [r.label for r in adapter_cv(self._offre_bi(), profile).rubriques] == bi

    def test_le_profil_cite_les_competences_communes(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv
        from freelance_radar.pipeline.score import score_offer

        offre = score_offer(make_offer(skills=["Python", "SQL"]), profile, cfg)
        a = adapter_cv(offre, profile)
        assert "Python" in a.profil.texte()

    def test_le_decoupage_du_profil_suit_la_maquette(self, cfg, profile):
        # 5 zones : la mise en forme du CV (gras) tient a ce decoupage.
        from freelance_radar.apply.cv import adapter_cv, zones_profil

        zones = zones_profil(adapter_cv(self._offre_bi(), profile).profil)
        assert len(zones) == 5
        assert len(zones[0]) == 1                    # l'initiale est isolee
        assert "".join(zones).startswith("Ingénieur")

    def test_les_competences_forment_des_paires(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv, zones_competences

        a = adapter_cv(self._offre_bi(), profile)
        zones = zones_competences(a.rubriques)
        assert len(zones) == 2 * len(a.rubriques)
        # Les zones paires portent un intitule, les impaires les outils.
        assert all(z.strip().endswith(":") for z in zones[::2])
        assert not zones[0].startswith("\n") and zones[2].startswith("\n")

    def test_plan_canva_absent_sans_cartographie(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv, plan_canva

        profile.documents = {"cv_pdf": "x.pdf"}
        a = adapter_cv(self._offre_bi(), profile)
        assert plan_canva(self._offre_bi(), profile, a) is None

    def test_le_dossier_contient_le_cv_adapte(self, cfg, profile):
        app = ApplicationGenerator(cfg, profile).generate(self._offre_bi(),
                                                          force_template=True)
        contenu = (Path(app.file_path) / "cv-adapte.md").read_text(encoding="utf-8")
        assert "Ordre des rubriques" in contenu
        assert "laissés intacts" in contenu
