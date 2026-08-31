"""Interface web : routes, actions et garde-fous (aucun serveur lance)."""

from __future__ import annotations

import pytest
from conftest import make_offer
from fastapi.testclient import TestClient

from freelance_radar.models import ApplicationStatus
from freelance_radar.pipeline.score import score_offer
from freelance_radar.storage import Database
from freelance_radar.web.app import create_app


@pytest.fixture
def offre_en_base(cfg, profile):
    db = Database(cfg.db_path)
    offre = score_offer(make_offer(), profile, cfg)
    db.upsert_offers([offre])
    db.close()
    return offre


@pytest.fixture
def client(cfg, profile):
    return TestClient(create_app(cfg, profile))


class TestConsultation:
    def test_accueil(self, client, offre_en_base):
        r = client.get("/")
        assert r.status_code == 200
        assert offre_en_base.title in r.text

    def test_filtre_par_score(self, client, offre_en_base):
        assert offre_en_base.title not in client.get("/?score_min=99").text
        assert offre_en_base.title in client.get("/?score_min=0").text

    def test_detail_offre(self, client, offre_en_base):
        r = client.get(f"/offre/{offre_en_base.id}")
        assert r.status_code == 200
        assert offre_en_base.company in r.text
        assert "Detail du score" in r.text or "score" in r.text.lower()

    def test_offre_inconnue(self, client):
        assert client.get("/offre/inexistante").status_code == 404

    def test_pipeline_vide(self, client, offre_en_base):
        r = client.get("/candidatures")
        assert r.status_code == 200 and "Aucune candidature" in r.text


class TestActions:
    def test_changer_statut(self, client, cfg, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/statut",
                        data={"statut": "sent"}, follow_redirects=False)
        assert r.status_code == 303
        db = Database(cfg.db_path)
        assert db.get_offer(offre_en_base.id).status == ApplicationStatus.SENT
        db.close()

    def test_statut_invalide_refuse(self, client, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/statut", data={"statut": "nimporte"})
        assert r.status_code == 400

    def test_generer_un_brouillon(self, client, cfg, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/candidature",
                        data={"moteur": "template"}, follow_redirects=False)
        assert r.status_code == 303
        db = Database(cfg.db_path)
        candidature = db.get_application(offre_en_base.id)
        db.close()
        assert candidature is not None
        # Garde-fou : generer ne doit jamais marquer une candidature envoyee.
        assert candidature.status == ApplicationStatus.DRAFTED
        assert candidature.sent_at is None


class TestDocuments:
    def test_lecture_d_un_document(self, client, offre_en_base):
        client.post(f"/offre/{offre_en_base.id}/candidature", data={"moteur": "template"})
        r = client.get(f"/document/{offre_en_base.id}/lettre.md")
        assert r.status_code == 200 and "Bonjour" in r.text

    def test_cv_adapte_consultable(self, client, offre_en_base):
        # Le CV adapte est l'etape manuelle du parcours : il doit etre a portee
        # de clic depuis l'offre, pas seulement sur le disque.
        client.post(f"/offre/{offre_en_base.id}/candidature", data={"moteur": "template"})
        r = client.get(f"/document/{offre_en_base.id}/cv-adapte.md")
        assert r.status_code == 200
        assert "composées pour cette offre" in r.text

    def test_nom_hors_liste_refuse(self, client, offre_en_base):
        client.post(f"/offre/{offre_en_base.id}/candidature", data={"moteur": "template"})
        assert client.get(f"/document/{offre_en_base.id}/offre.json").status_code == 404

    @pytest.mark.parametrize("chemin", [
        "../../../.env",
        "..%2f..%2fconfig%2fprofile.yaml",
        "lettre.md/../../../.env",
    ])
    def test_traversee_de_repertoire_bloquee(self, client, offre_en_base, chemin):
        # Sans validation, un nom construit pourrait sortir du dossier des
        # candidatures et lire .env ou le profil.
        client.post(f"/offre/{offre_en_base.id}/candidature", data={"moteur": "template"})
        r = client.get(f"/document/{offre_en_base.id}/{chemin}")
        assert r.status_code == 404
        assert "ANTHROPIC" not in r.text and "daily_rate_target" not in r.text

    def test_document_sans_candidature(self, client, offre_en_base):
        assert client.get(f"/document/{offre_en_base.id}/lettre.md").status_code == 404


class TestCampagne:
    def test_etat_initial(self, client):
        etat = client.get("/campagne/etat").json()
        assert etat["running"] is False and etat["result"] == {}


class TestMasquageDesIdentifiants:
    """Les cles ne doivent jamais atterrir dans un journal.

    Adzuna impose ses identifiants en parametres d'URL, et httpx journalise
    l'URL complete au niveau INFO : sans masquage, `radar scrape -v` les
    affichait en clair dans la console.
    """

    def test_url_avec_cles(self):
        from freelance_radar.secrets import redact

        masque = redact("GET https://api.adzuna.com/v1/api/jobs/fr/search/1"
                        "?app_id=abc123&app_key=secret456&what=data")
        assert "secret456" not in masque and "abc123" not in masque
        assert "what=data" in masque   # le reste de l'URL reste lisible

    def test_entete_authorization(self):
        from freelance_radar.secrets import redact
        assert "eyJhbGci" not in redact("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.x")

    def test_argument_non_textuel(self, caplog):
        """httpx passe un objet URL, pas une chaine : le filtre doit quand meme masquer."""
        import logging

        from freelance_radar.secrets import RedactingFilter

        class FausseUrl:
            def __str__(self):
                return "https://api.example.com/x?app_key=tressecret"

        logger = logging.getLogger("test.masquage")
        logger.addFilter(RedactingFilter())
        with caplog.at_level(logging.INFO, logger="test.masquage"):
            logger.info("HTTP Request: GET %s", FausseUrl())
        assert "tressecret" not in caplog.text
        assert "***" in caplog.text


class TestAnnotations:
    """Annoter, selectionner, ecarter : le travail manuel sur les offres."""

    def test_enregistrer_une_note(self, client, cfg, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/note",
                        data={"note": "Relancer Marie le 15"}, follow_redirects=False)
        assert r.status_code == 303
        db = Database(cfg.db_path)
        assert db.get_offer(offre_en_base.id).notes == "Relancer Marie le 15"
        db.close()

    def test_basculer_la_selection(self, client, cfg, offre_en_base):
        client.post(f"/offre/{offre_en_base.id}/selection")
        db = Database(cfg.db_path)
        assert db.get_offer(offre_en_base.id).starred is True
        db.close()
        client.post(f"/offre/{offre_en_base.id}/selection")
        db = Database(cfg.db_path)
        assert db.get_offer(offre_en_base.id).starred is False
        db.close()

    def test_ecarter_masque_l_offre(self, client, cfg, offre_en_base):
        client.post(f"/offre/{offre_en_base.id}/ecarter")
        assert offre_en_base.title not in client.get("/").text
        # ... mais elle reste consultable via le filtre dedie
        assert offre_en_base.title in client.get("/?ecartees=true").text

    def test_une_offre_ecartee_ne_revient_pas_apres_une_campagne(self, cfg, profile,
                                                                 offre_en_base):
        """Une suppression reelle serait annulee au prochain scrape.

        C'est pourquoi `discard` masque au lieu de supprimer : la ligne reste
        en base comme memoire de la decision.
        """
        from conftest import make_offer

        db = Database(cfg.db_path)
        db.discard(offre_en_base.id)
        db.upsert_offers([make_offer()])          # la campagne suivante la revoit
        relue = db.get_offer(offre_en_base.id)
        assert relue.discarded is True
        assert relue.id not in [o.id for o in db.list_offers(limit=50)]
        db.close()

    def test_une_campagne_n_ecrase_pas_les_annotations(self, cfg, offre_en_base):
        from conftest import make_offer

        db = Database(cfg.db_path)
        db.set_note(offre_en_base.id, "mon analyse")
        db.toggle_star(offre_en_base.id)
        db.upsert_offers([make_offer()])
        relue = db.get_offer(offre_en_base.id)
        assert relue.notes == "mon analyse" and relue.starred is True
        db.close()

    def test_filtre_selection(self, client, cfg, offre_en_base):
        assert "Aucune offre" in client.get("/?selection=true").text
        client.post(f"/offre/{offre_en_base.id}/selection")
        assert offre_en_base.title in client.get("/?selection=true").text

    def test_bouton_j_ai_postule(self, client, cfg, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/postuler", follow_redirects=False)
        assert r.status_code == 303
        db = Database(cfg.db_path)
        assert db.get_offer(offre_en_base.id).status == ApplicationStatus.SENT
        db.close()

    def test_actions_sur_offre_inconnue(self, client):
        for route in ("note", "selection", "ecarter", "postuler"):
            assert client.post(f"/offre/inexistante/{route}",
                               data={"note": "x"}).status_code == 404
