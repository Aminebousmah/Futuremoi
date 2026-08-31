"""Scoring : verifie que les signaux vont dans le bon sens et restent bornes."""

from __future__ import annotations

from conftest import make_offer

from freelance_radar.models import RemotePolicy
from freelance_radar.pipeline.score import SCORE_SANS_COMPETENCES, rank, score_offer


class TestScore:
    def test_score_borne_entre_0_et_100(self, cfg, profile):
        offre = score_offer(make_offer(), profile, cfg)
        assert 0 <= offre.score <= 100

    def test_stack_alignee_score_mieux_qu_une_stack_etrangere(self, cfg, profile):
        aligne = score_offer(make_offer(skills=["Python", "SQL", "dbt"]), profile, cfg)
        etranger = score_offer(make_offer(skills=["Qlik", "SAS", "Talend"]), profile, cfg)
        assert aligne.score > etranger.score

    def test_tjm_superieur_a_l_objectif_score_mieux(self, cfg, profile):
        haut = score_offer(make_offer(daily_rate_min=700, daily_rate_max=700), profile, cfg)
        bas = score_offer(make_offer(daily_rate_min=520, daily_rate_max=520), profile, cfg)
        assert haut.score > bas.score

    def test_offre_recente_score_mieux_qu_une_ancienne(self, cfg, profile):
        from datetime import datetime, timedelta, timezone
        recente = score_offer(make_offer(), profile, cfg)
        ancienne = score_offer(
            make_offer(published_at=datetime.now(timezone.utc) - timedelta(days=25)),
            profile, cfg)
        assert recente.score > ancienne.score

    def test_detail_expose_les_signaux_et_les_ecarts(self, cfg, profile):
        offre = score_offer(make_offer(skills=["Python", "Kafka"]), profile, cfg)
        detail = offre.score_detail
        assert set(cfg.scoring.weights) <= set(detail)
        assert "Python" in detail["_matched_skills"]
        assert "Kafka" in detail["_missing_skills"]

    def test_annonce_sans_competence_ecarte_le_signal(self, cfg, profile):
        """Une annonce vague n'a pas de signal competences : il est retire.

        Le noter 0.5 revenait a punir le silence comme un desaccord, alors que
        la plupart des annonces sont muettes sur la plupart des signaux.
        """
        offre = score_offer(make_offer(skills=[], description="Mission data."), profile, cfg)
        assert offre.score_detail["skills_match"] is None
        assert "skills_match" in offre.score_detail["_signaux_ignores"]
        # Le score reste calcule sur ce que l'on sait, pas mis a zero.
        assert offre.score > 0

    def test_remote_incompatible_penalise(self, cfg, profile):
        # profil "hybrid" : le presentiel strict doit couter des points
        sur_site = score_offer(make_offer(remote=RemotePolicy.ONSITE), profile, cfg)
        hybride = score_offer(make_offer(remote=RemotePolicy.HYBRID), profile, cfg)
        assert hybride.score > sur_site.score

    def test_rank_trie_par_score_decroissant(self, cfg, profile):
        offres = rank([
            make_offer(title="Data A", skills=["Qlik"], daily_rate_min=500, daily_rate_max=500),
            make_offer(title="Data B", skills=["Python", "SQL", "dbt"],
                       daily_rate_min=700, daily_rate_max=700),
        ], profile, cfg)
        assert [o.title for o in offres] == ["Data B", "Data A"]


class TestSignauxInconnus:
    """Un signal absent est retire du calcul, pas note 0.5.

    Punir le silence comme un desaccord plafonnait les scores : sur le corpus
    reel, seules 13 % des annonces disent leur politique de teletravail.
    """

    def test_signal_absent_vaut_none(self, cfg, profile):
        offre = score_offer(make_offer(remote=RemotePolicy.UNKNOWN,
                                       duration_months=None), profile, cfg)
        assert offre.score_detail["remote"] is None
        assert offre.score_detail["duration"] is None
        assert {"remote", "duration"} <= set(offre.score_detail["_signaux_ignores"])

    def test_le_silence_ne_penalise_plus(self, cfg, profile):
        """Deux offres identiques, l'une muette sur le teletravail.

        L'offre muette ne doit pas etre notee comme une offre defavorable.
        """
        muette = score_offer(make_offer(remote=RemotePolicy.UNKNOWN), profile, cfg)
        sur_site = score_offer(make_offer(remote=RemotePolicy.ONSITE), profile, cfg)
        assert muette.score > sur_site.score

    def test_poids_couvert_reflete_la_confiance(self, cfg, profile):
        """Un score appuye sur deux signaux ne vaut pas un score sur cinq."""
        complete = score_offer(
            make_offer(remote=RemotePolicy.HYBRID, duration_months=6,
                       daily_rate_min=600, daily_rate_max=600), profile, cfg)
        creuse = score_offer(
            make_offer(remote=RemotePolicy.UNKNOWN, duration_months=None), profile, cfg)
        assert complete.score_detail["_poids_couvert"] == 1.0
        assert creuse.score_detail["_poids_couvert"] < 1.0

    def test_annonce_totalement_muette(self, cfg, profile):
        """Sans aucun signal, rien ne permet de recommander l'offre."""
        offre = make_offer(skills=[], description="", remote=RemotePolicy.UNKNOWN,
                           duration_months=None, daily_rate_min=None,
                           daily_rate_max=None)
        offre.published_at = None
        offre = score_offer(offre, profile, cfg)
        assert offre.score == 0.0
        assert offre.score_detail["_poids_couvert"] == 0.0

    def test_sans_competences_le_score_est_plafonne(self, cfg, profile):
        """Un bon TJM ne suffit pas a recommander une offre incomprehensible.

        Sans ce plafond, une annonce dont seul le TJM etait lisible obtenait
        100/100 : le signal etait excellent, mais il ne disait rien du poste.
        """
        offre = make_offer(skills=[], description="", remote=RemotePolicy.UNKNOWN,
                           duration_months=None,
                           daily_rate_min=900, daily_rate_max=900)
        offre.published_at = None
        offre = score_offer(offre, profile, cfg)
        assert offre.score_detail["skills_match"] is None
        assert offre.score_detail["daily_rate"] == 1.0
        assert offre.score <= SCORE_SANS_COMPETENCES
        assert offre.score < cfg.scoring.apply_threshold

    def test_le_plafond_artificiel_a_disparu(self, cfg, profile):
        """Un recoupement parfait sur une annonce muette plafonnait a 76."""
        offre = score_offer(
            make_offer(skills=["Python", "SQL", "dbt"], remote=RemotePolicy.UNKNOWN,
                       duration_months=None), profile, cfg)
        assert offre.score_detail["skills_match"] == 1.0
        assert offre.score > 90
