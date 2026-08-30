"""Scoring : verifie que les signaux vont dans le bon sens et restent bornes."""

from __future__ import annotations

from conftest import make_offer

from freelance_radar.models import RemotePolicy
from freelance_radar.pipeline.score import rank, score_offer


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

    def test_annonce_sans_competence_reste_neutre(self, cfg, profile):
        # Une annonce vague ne doit etre ni favorisee ni condamnee.
        offre = score_offer(make_offer(skills=[], description="Mission data."), profile, cfg)
        assert offre.score_detail["skills_match"] == 0.5

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
