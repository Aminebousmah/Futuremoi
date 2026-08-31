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


class TestFicheCandidature:
    """La fiche rassemble ce qu'un formulaire d'employeur reclame."""

    def test_la_fiche_s_affiche(self, client, offre_en_base):
        r = client.get(f"/offre/{offre_en_base.id}/fiche")
        assert r.status_code == 200
        for attendu in ("Prénom", "E-mail", "Téléphone", "LinkedIn",
                        "TJM / prétentions", "Disponibilité"):
            assert attendu in r.text, attendu

    def test_les_valeurs_du_profil_y_sont(self, client, offre_en_base):
        r = client.get(f"/offre/{offre_en_base.id}/fiche")
        assert "Ada" in r.text and "ada@example.com" in r.text

    def test_offre_inconnue(self, client):
        assert client.get("/offre/inexistante/fiche").status_code == 404

    def test_rien_n_est_soumis_depuis_la_fiche(self, client, offre_en_base):
        # Garde-fou : la page ne doit contenir aucun formulaire d'envoi.
        r = client.get(f"/offre/{offre_en_base.id}/fiche")
        assert "<form" not in r.text.split("<footer")[0].split('id="btn-campagne"')[-1]


class TestFavoriRemplissage:
    """Le favori qui pré-remplit un formulaire d'employeur."""

    @staticmethod
    def _script(profile):
        from urllib.parse import unquote

        from freelance_radar.web.bookmarklet import construire

        favori = construire(profile)
        assert favori.startswith("javascript:")
        return unquote(favori[len("javascript:"):])

    def test_les_sauts_de_ligne_sont_conserves(self, profile):
        """Regression : compacter le script cassait le favori.

        Joindre les lignes par des espaces transformait chaque commentaire de
        fin de ligne en bâillon — tout ce qui suivait se retrouvait commenté et
        le script ne s'exécutait plus.
        """
        script = self._script(profile)
        assert script.count("\n") > 20
        for ligne in script.splitlines():
            avant, sep, apres = ligne.partition("//")
            if sep and "http" not in avant:
                assert not apres.strip().startswith("var "), ligne

    def test_le_script_est_syntaxiquement_valide(self, profile):
        # Equilibrage des delimiteurs : un script casse ne leve aucune erreur
        # a la generation, seulement au clic dans le navigateur.
        script = self._script(profile)
        for ouvrant, fermant in (("(", ")"), ("{", "}"), ("[", "]")):
            assert script.count(ouvrant) == script.count(fermant), ouvrant

    def test_ne_soumet_jamais_le_formulaire(self, profile):
        script = self._script(profile)
        for interdit in ("submit()", ".click()", "form.submit", "requestSubmit"):
            assert interdit not in script, interdit

    def test_ne_sort_pas_du_navigateur(self, profile):
        # Les donnees vivent dans le favori : aucun appel reseau.
        script = self._script(profile)
        for interdit in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon"):
            assert interdit not in script, interdit

    def test_les_valeurs_du_profil_y_sont(self, profile):
        script = self._script(profile)
        assert "ada@example.com" in script and "Ada" in script

    def test_les_champs_sensibles_sont_ecartes(self, profile):
        script = self._script(profile)
        assert "password" in script          # présent dans la liste des types ignorés
        assert "ignores" in script

    def test_la_page_outils_propose_le_favori(self, client):
        r = client.get("/outils")
        assert r.status_code == 200
        assert "javascript:" in r.text
        assert "ne soumet jamais" in r.text


class TestConsentementLLM:
    """La generation par defaut ne doit jamais toucher a l'API Anthropic."""

    def test_bouton_par_defaut_hors_ligne(self, client, offre_en_base):
        """Le bouton principal poste moteur=template : aucun appel possible."""
        page = client.get(f"/offre/{offre_en_base.id}").text
        assert 'name="moteur" value="template"' in page
        assert "candidature/confirmer" in page

    def test_ecran_annonce_le_cout(self, client, offre_en_base):
        r = client.get(f"/offre/{offre_en_base.id}/candidature/confirmer")
        assert r.status_code == 200
        assert "factur" in r.text.lower()

    def test_ecran_sans_moteur_actif_ne_propose_rien(self, client, offre_en_base):
        """use_llm=false dans la config : pas de bouton, donc pas d'appel possible."""
        r = client.get(f"/offre/{offre_en_base.id}/candidature/confirmer")
        assert 'name="confirmation"' not in r.text
        assert "desactive" in r.text.lower()

    def test_ecran_porte_le_second_jeton(self, cfg, profile, offre_en_base, monkeypatch):
        """Moteur autorise et disponible : l'ecran expose la validation finale."""
        from freelance_radar.apply.llm import LLMWriter

        cfg.application.use_llm = True
        monkeypatch.setattr(LLMWriter, "blocked_reason", lambda self: None)
        client = TestClient(create_app(cfg, profile))

        r = client.get(f"/offre/{offre_en_base.id}/candidature/confirmer")
        assert r.status_code == 200
        assert 'name="confirmation" value="oui"' in r.text
        assert 'name="moteur" value="llm"' in r.text

    def test_confirmation_offre_inconnue(self, client):
        assert client.get("/offre/inconnue/candidature/confirmer").status_code == 404

    def test_post_sans_confirmation_reste_hors_ligne(
        self, client, offre_en_base, monkeypatch
    ):
        """Un POST forge avec moteur=llm mais sans jeton retombe sur les templates."""
        from freelance_radar.apply.llm import LLMWriter

        def interdit(self, prompt):  # pragma: no cover - ne doit jamais s'executer
            raise AssertionError("appel LLM declenche sans confirmation")

        monkeypatch.setattr(LLMWriter, "write", interdit)
        r = client.post(f"/offre/{offre_en_base.id}/candidature",
                        data={"moteur": "llm"}, follow_redirects=False)
        assert r.status_code == 303

    def test_post_par_defaut_reste_hors_ligne(self, client, offre_en_base, monkeypatch):
        from freelance_radar.apply.llm import LLMWriter

        def interdit(self, prompt):  # pragma: no cover - ne doit jamais s'executer
            raise AssertionError("appel LLM declenche par la generation par defaut")

        monkeypatch.setattr(LLMWriter, "write", interdit)
        r = client.post(f"/offre/{offre_en_base.id}/candidature",
                        data={}, follow_redirects=False)
        assert r.status_code == 303


class TestFicheEntretien:
    def test_bouton_present_sur_la_page(self, client, offre_en_base):
        page = client.get(f"/offre/{offre_en_base.id}").text
        assert f"/offre/{offre_en_base.id}/entretien" in page
        assert "fiche d'entretien" in page

    def test_generation_ecrit_le_fichier(self, client, offre_en_base, cfg):
        r = client.post(f"/offre/{offre_en_base.id}/entretien",
                        follow_redirects=False)
        assert r.status_code == 303
        fiches = list(cfg.applications_path.glob("*/entretien.md"))
        assert len(fiches) == 1
        assert "Préparation d'entretien" in fiches[0].read_text(encoding="utf-8")

    def test_redirige_vers_le_document(self, client, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/entretien",
                        follow_redirects=False)
        assert r.headers["location"].endswith("/entretien.md")

    def test_offre_inconnue(self, client):
        assert client.post("/offre/inconnue/entretien").status_code == 404

    def test_aucun_appel_llm(self, client, offre_en_base, monkeypatch):
        """La fiche est hors ligne : elle ne doit toucher a aucune cle."""
        from freelance_radar.apply.llm import LLMWriter

        def interdit(self, prompt):  # pragma: no cover - ne doit jamais s'executer
            raise AssertionError("la fiche d'entretien a declenche un appel LLM")

        monkeypatch.setattr(LLMWriter, "write", interdit)
        r = client.post(f"/offre/{offre_en_base.id}/entretien",
                        follow_redirects=False)
        assert r.status_code == 303


class TestRegenerationCV:
    """Le CV doit pouvoir etre rejoue seul, apres edition du profil."""

    def _avec_candidature(self, client, offre):
        client.post(f"/offre/{offre.id}/candidature", data={"moteur": "template"})

    def test_bouton_present(self, client, offre_en_base):
        self._avec_candidature(client, offre_en_base)
        page = client.get(f"/offre/{offre_en_base.id}").text
        assert f"/offre/{offre_en_base.id}/cv" in page
        assert "Régénérer le CV" in page

    def test_regeneration_ecrit_le_pdf(self, client, cfg, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/cv", follow_redirects=False)
        assert r.status_code == 303
        pdfs = list(cfg.applications_path.glob("*/cv.pdf"))
        if not pdfs:
            pytest.skip("aucune police système compatible sur cette machine")
        assert pdfs[0].read_bytes().startswith(b"%PDF-")

    def test_redirige_vers_le_pdf(self, client, offre_en_base):
        r = client.post(f"/offre/{offre_en_base.id}/cv", follow_redirects=False)
        assert r.headers["location"].endswith("/cv.pdf")

    def test_offre_inconnue(self, client):
        assert client.post("/offre/inconnue/cv").status_code == 404

    def test_le_pdf_est_servi_en_binaire(self, client, offre_en_base):
        """Un PDF ne doit pas passer par le gabarit de lecture, qui attend du texte."""
        self._avec_candidature(client, offre_en_base)
        client.post(f"/offre/{offre_en_base.id}/cv")
        r = client.get(f"/document/{offre_en_base.id}/cv.pdf")
        if r.status_code == 404:
            pytest.skip("aucune police système compatible")
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF-")

    def test_traversee_toujours_bloquee(self, client, offre_en_base):
        self._avec_candidature(client, offre_en_base)
        r = client.get(f"/document/{offre_en_base.id}/../../../.env")
        assert r.status_code == 404
