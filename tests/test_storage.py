"""Persistance : le round-trip est le point ou les typages laxistes cassent."""

from __future__ import annotations

from conftest import make_offer

from freelance_radar.models import Application, ApplicationStatus
from freelance_radar.pipeline.score import score_offer
from freelance_radar.storage import Database


class TestRoundTrip:
    def test_offre_relue_identique(self, cfg):
        db = Database(cfg.db_path)
        original = make_offer()
        db.upsert_offers([original])
        relue = db.get_offer(original.id)
        assert relue is not None
        for champ in ("title", "company", "url", "location", "remote", "contract",
                      "daily_rate_min", "daily_rate_max", "duration_months", "skills"):
            assert getattr(relue, champ) == getattr(original, champ), champ
        db.close()

    def test_score_detail_avec_listes_survit_au_round_trip(self, cfg, profile):
        # Regression : score_detail melange des flottants et des listes de
        # competences ; un typage trop strict faisait echouer la relecture.
        db = Database(cfg.db_path)
        offre = score_offer(make_offer(skills=["Python", "Kafka"]), profile, cfg)
        db.upsert_offers([offre])
        relue = db.get_offer(offre.id)
        assert relue.score_detail["_matched_skills"] == ["Python"]
        assert isinstance(relue.score_detail["skills_match"], float)
        db.close()

    def test_prefixe_d_identifiant_accepte(self, cfg):
        db = Database(cfg.db_path)
        offre = make_offer()
        db.upsert_offers([offre])
        assert db.get_offer(offre.id[:8]).id == offre.id
        db.close()


class TestUpsert:
    def test_compte_nouvelles_et_connues(self, cfg):
        db = Database(cfg.db_path)
        offre = make_offer()
        assert db.upsert_offers([offre]) == (1, 0)
        assert db.upsert_offers([offre]) == (0, 1)
        db.close()

    def test_le_statut_conserve_est_repercute_sur_l_objet_en_memoire(self, cfg):
        # Le recapitulatif affiche apres une campagne lit les objets en memoire :
        # ils doivent porter le statut reel, pas le "new" par defaut.
        db = Database(cfg.db_path)
        offre = make_offer()
        db.upsert_offers([offre])
        db.set_status(offre.id, ApplicationStatus.SENT)
        rescrapee = make_offer()
        db.upsert_offers([rescrapee])
        assert rescrapee.status == ApplicationStatus.SENT
        db.close()

    def test_le_statut_survit_a_une_nouvelle_campagne(self, cfg):
        # Une offre deja envoyee ne doit pas repasser en "new" au prochain scrape.
        db = Database(cfg.db_path)
        offre = make_offer()
        db.upsert_offers([offre])
        db.set_status(offre.id, ApplicationStatus.SENT)
        db.upsert_offers([make_offer()])
        assert db.get_offer(offre.id).status == ApplicationStatus.SENT
        db.close()


class TestCandidatures:
    def test_sauvegarde_et_relecture(self, cfg):
        db = Database(cfg.db_path)
        offre = make_offer()
        db.upsert_offers([offre])
        db.save_application(Application(
            offer_id=offre.id, subject="Objet", cover_letter="Lettre",
            email_body="Email", highlights=["Python"], gaps=["Kafka"],
            proposed_rate=650, generator="template",
        ))
        app = db.get_application(offre.id)
        assert app.highlights == ["Python"] and app.proposed_rate == 650
        # L'offre passe automatiquement en "drafted"
        assert db.get_offer(offre.id).status == ApplicationStatus.DRAFTED
        db.close()

    def test_pipeline_ne_liste_que_les_offres_candidatees(self, cfg):
        db = Database(cfg.db_path)
        avec = make_offer(title="Data avec candidature")
        sans = make_offer(title="Data sans candidature")
        db.upsert_offers([avec, sans])
        db.save_application(Application(offer_id=avec.id))
        pipeline = db.pipeline()
        assert [o.title for o, _ in pipeline] == ["Data avec candidature"]
        db.close()

    def test_historique_des_campagnes(self, cfg):
        db = Database(cfg.db_path)
        db.log_run(["freework"], fetched=50, kept=10, new_offers=7, detail="ok")
        run = db.last_runs(1)[0]
        assert run["fetched"] == 50 and run["new_offers"] == 7
        db.close()
