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
    """Le CV est COMPOSE a partir de l'offre, pas simplement reordonne.

    Le generateur lit ce que l'offre demande, le traduit en outils de
    l'inventaire du profil, et nomme les rubriques en consequence.
    """

    @staticmethod
    def _offre_bi():
        return make_offer(
            title="Consultant Power BI senior",
            description="Concevoir des tableaux de bord et faire evoluer les rapports.",
            skills=["Power BI", "Tableau"],
        )

    @staticmethod
    def _offre_ml():
        return make_offer(
            title="Data Scientist",
            description="Modeles predictifs et scoring, machine learning en production.",
            skills=["Machine Learning", "Python"],
        )

    def test_les_rubriques_dependent_de_l_offre(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        bi = [r.label for r in adapter_cv(self._offre_bi(), profile).rubriques]
        ml = [r.label for r in adapter_cv(self._offre_ml(), profile).rubriques]
        assert bi != ml, "deux offres differentes doivent donner deux CV differents"

    def test_la_rubrique_demandee_passe_en_tete(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        mobiles = [r.label for r in a.rubriques if not r.epinglee]
        assert mobiles[0] == "Visualisation & BI"

    def test_les_competences_demandees_arrivent_en_tete(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        premiere = next(r for r in a.rubriques if not r.epinglee)
        assert premiere.outils[0] == "Power BI"

    def test_on_deborde_un_peu_du_strict_demande(self, cfg, profile):
        # L'annonce ne cite que Power BI : le CV doit quand meme montrer
        # l'etendue du profil, sans s'eloigner du poste.
        from freelance_radar.apply.cv import COMPETENCES_RETENUES, adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        total = sum(len(r.outils) for r in a.rubriques if not r.epinglee)
        assert total > 4
        assert total <= COMPETENCES_RETENUES + 4

    def test_aucune_rubrique_squelettique(self, cfg, profile):
        from freelance_radar.apply.cv import MINIMUM_PAR_RUBRIQUE, adapter_cv

        for offre in (self._offre_bi(), self._offre_ml()):
            for r in adapter_cv(offre, profile).rubriques:
                assert len(r.outils) >= MINIMUM_PAR_RUBRIQUE, r.label

    def test_aucune_competence_hors_de_sa_categorie(self, cfg, profile):
        # Replier une categorie dans une autre rangerait ses competences sous
        # un intitule qui ment.
        from freelance_radar.apply.cv import adapter_cv

        inventaire = profile.cv["competences"]
        for r in adapter_cv(self._offre_bi(), profile).rubriques:
            if r.label in inventaire:
                for outil in r.outils:
                    assert outil in inventaire[r.label], f"{outil} sous {r.label}"

    def test_les_savoir_etre_suivent_l_offre(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        pedago = make_offer(title="Data Analyst",
                            description="Formation et accompagnement des equipes metier.")
        neutre = make_offer(title="Data Analyst", description="Analyse de donnees.")
        a = next(r for r in adapter_cv(pedago, profile).rubriques
                 if r.label == "Compétences transverses")
        b = next(r for r in adapter_cv(neutre, profile).rubriques
                 if r.label == "Compétences transverses")
        assert a.outils != b.outils
        assert "Pédagogie et formation" in a.outils

    def test_les_rubriques_fixes_restent_en_tete(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        a = adapter_cv(self._offre_bi(), profile)
        assert [r.label for r in a.rubriques[:2]] == ["Compétences transverses", "Langues"]
        assert all(r.epinglee for r in a.rubriques[:2])

    def test_aucun_outil_invente(self, cfg, profile):
        # Le generateur ne peut puiser que dans l'inventaire declare.
        from freelance_radar.apply.cv import adapter_cv

        inventaire = {o for liste in profile.cv["competences"].values() for o in liste}
        inventaire |= set(profile.cv["transverses"])
        inventaire |= {o for r in profile.cv["rubriques_fixes"] for o in r["outils"]}
        for r in adapter_cv(self._offre_ml(), profile).rubriques:
            for outil in r.outils:
                assert outil in inventaire, outil

    def test_aucun_outil_repete_entre_rubriques(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        vus = [o for r in adapter_cv(self._offre_bi(), profile).rubriques
               if not r.epinglee for o in r.outils]
        assert len(vus) == len(set(vus))

    def test_le_bloc_tient_dans_la_maquette(self, cfg, profile):
        # Le CV reserve 7 lignes aux competences : deux fixes, cinq composables.
        from freelance_radar.apply.cv import RUBRIQUES_MOBILES, adapter_cv

        for offre in (self._offre_bi(), self._offre_ml()):
            a = adapter_cv(offre, profile)
            assert len([r for r in a.rubriques if not r.epinglee]) <= RUBRIQUES_MOBILES

    def test_profil_sans_inventaire_ne_casse_pas(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        profile.cv = {}
        a = adapter_cv(self._offre_bi(), profile)
        assert a.rubriques == [] and a.profil is not None

    def test_pas_de_fuite_d_etat_entre_deux_offres(self, cfg, profile):
        from freelance_radar.apply.cv import adapter_cv

        bi = [r.label for r in adapter_cv(self._offre_bi(), profile).rubriques]
        adapter_cv(self._offre_ml(), profile)
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
        assert "composées pour cette offre" in contenu
        assert "jamais touchés" in contenu


class TestFiche:
    """Fiche de candidature : les champs qu'un formulaire reclame."""

    def test_prenom_et_nom_separes(self, cfg, profile):
        fiche = ApplicationGenerator(cfg, profile).fiche_candidature(make_offer())
        valeurs = {c.cle: c.valeur for c in fiche.identite}
        assert valeurs["prenom"] == "Ada" and valeurs["nom"] == "Test"

    def test_nom_en_un_seul_mot(self, cfg, profile):
        from freelance_radar.apply.candidature import _nom_prenom
        assert _nom_prenom("Prince") == ("Prince", "")

    def test_le_tjm_reprend_celui_de_la_lettre(self, cfg, profile):
        offre = make_offer(daily_rate_min=900, daily_rate_max=900)
        gen = ApplicationGenerator(cfg, profile)
        app = gen.generate(offre, force_template=True)
        fiche = gen.fiche_candidature(offre)
        tjm = next(c.valeur for c in fiche.mission if c.cle == "tjm")
        assert str(app.proposed_rate) in tjm

    def test_url_nettoyee_de_son_tracking(self, cfg, profile):
        # Le lien part chez un client : il ne doit pas trainer notre identifiant
        # d'application Adzuna.
        from freelance_radar.apply.candidature import url_propre
        sale = "https://www.adzuna.fr/details/123?utm_medium=api&utm_source=cc8f9a36&x=1"
        propre = url_propre(sale)
        assert "utm_source" not in propre and "cc8f9a36" not in propre
        assert propre.endswith("x=1")

    def test_une_question_d_experience_sur_la_competence_commune(self, cfg, profile):
        from freelance_radar.pipeline.score import score_offer

        offre = score_offer(make_offer(skills=["dbt", "Snowflake"]), profile, cfg)
        fiche = ApplicationGenerator(cfg, profile).fiche_candidature(offre)
        libelles = [c.libelle for c in fiche.questions]
        assert any("expérience sur" in lib for lib in libelles)

    def test_la_preuve_vient_des_references_reelles(self, cfg, profile):
        from freelance_radar.pipeline.score import score_offer

        offre = score_offer(make_offer(skills=["dbt"]), profile, cfg)
        fiche = ApplicationGenerator(cfg, profile).fiche_candidature(offre)
        experience = next(c.valeur for c in fiche.questions if "expérience sur" in c.libelle)
        assert "Retailer" in experience      # la seule reference du profil de test


class TestCompetencesAttendues:
    """Le titre du poste compte, pas seulement le corps de l'annonce.

    Beaucoup de sources tronquent leur texte — l'API Adzuna rend 500
    caracteres. Sur une annonce "Data engineer" ainsi coupee, aucun outil
    n'etait cite, toutes les competences tombaient a zero, et la composition
    se rabattait sur le haut de l'inventaire : le CV d'une mission Data
    Engineer n'affichait ni dbt ni Airflow.
    """

    def test_le_titre_fait_remonter_la_pile_du_metier(self, profile):
        from freelance_radar.apply.cv import composer_rubriques

        # Annonce volontairement muette : tout doit venir du titre.
        offre = make_offer(title="Data engineer",
                           description="Mission au sein de la direction data.",
                           skills=[])
        outils = {o for r in composer_rubriques(offre, profile) for o in r.outils}
        assert {"dbt", "Airflow"} & outils, outils

    def test_le_metier_oriente_le_classement(self, profile):
        """Sur la meme annonce muette, le titre change l'ordre des competences.

        On compare les rangs plutot que la presence : sur un inventaire reduit,
        le remplissage de fin de liste ramene de toute facon tout le monde.
        """
        from freelance_radar.apply.cv import classer_competences

        texte = "Mission au sein de la direction data."
        analyste = {c: s for _, c, s in classer_competences(
            make_offer(title="Data analyst", description=texte, skills=[]), profile)}
        engineer = {c: s for _, c, s in classer_competences(
            make_offer(title="Data engineer", description=texte, skills=[]), profile)}

        assert analyste["Power BI"] > analyste["Airflow"]
        assert engineer["Airflow"] > engineer["Power BI"]

    def test_une_mention_explicite_pese_plus_que_l_attendu(self, profile):
        """Le titre suggere ; l'annonce qui cite un outil doit primer."""
        from freelance_radar.apply.cv import classer_competences

        offre = make_offer(
            title="Data engineer",
            description="Nous cherchons un expert Power BI. Power BI au quotidien.",
            skills=["Power BI"])
        scores = {c: s for _, c, s in classer_competences(offre, profile)}
        assert scores["Power BI"] > scores["Airflow"]

    def test_famille_inconnue_ne_casse_rien(self, profile):
        from freelance_radar.apply.cv import classer_competences

        offre = make_offer(title="Chargé de mission", description="", skills=[])
        assert classer_competences(offre, profile)  # ne leve pas, rend l'inventaire

    def test_les_intitules_attendus_existent_dans_l_inventaire_reel(self):
        """Meme garde-fou que pour la fiche d'entretien : un nom mal
        orthographie ne declencherait jamais, sans erreur visible.
        """
        import pytest

        from freelance_radar.config import load_profile, project_root
        from freelance_radar.pipeline.enrich import COMPETENCES_ATTENDUES

        if not (project_root() / "config" / "profile.yaml").exists():
            pytest.skip("profile.yaml absent (fichier personnel, hors depot)")

        inventaire = {c for outils
                      in (load_profile().cv or {}).get("competences", {}).values()
                      for c in outils}
        assert inventaire, "inventaire vide : le test ne prouverait rien"
        for famille, attendues in COMPETENCES_ATTENDUES.items():
            inconnues = sorted(set(attendues) - inventaire)
            assert not inconnues, f"{famille} : intitulés hors inventaire {inconnues}"


class TestPortageSalarial:
    """Le statut ne doit pas etre code en dur : il depend du profil.

    Sans SIRET propre, c'est celui de la societe de portage que reclament les
    formulaires et les contrats -- mais il ne doit jamais etre presente comme
    une immatriculation personnelle.
    """

    def test_sans_portage_le_statut_reste_freelance(self, profile):
        profile.portage = {}
        assert profile.statut_juridique == "Freelance / independant"

    def test_le_portage_change_le_statut(self, profile):
        profile.portage = {"siret": "93520225900013"}
        assert profile.statut_juridique == "Portage salarial"

    def test_la_societe_est_nommee_si_connue(self, profile):
        profile.portage = {"siret": "93520225900013", "societe": "Ma Societe"}
        assert profile.statut_juridique == "Portage salarial (Ma Societe)"

    def test_le_siret_du_portage_sert_de_repli(self, profile):
        profile.identity = {**profile.identity, "siret": ""}
        profile.portage = {"siret": "93520225900013"}
        assert profile.siret == "93520225900013"

    def test_une_immatriculation_propre_a_la_priorite(self, profile):
        profile.identity = {**profile.identity, "siret": "11111111100011"}
        profile.portage = {"siret": "93520225900013"}
        assert profile.siret == "11111111100011"

    def test_la_fiche_annonce_l_origine_du_siret(self, cfg, profile):
        """Un numero emprunte doit etre signale comme tel."""
        from freelance_radar.apply import ApplicationGenerator

        profile.identity = {**profile.identity, "siret": ""}
        profile.portage = {"siret": "93520225900013"}
        fiche = ApplicationGenerator(cfg, profile).fiche_candidature(make_offer())
        champs = {c.cle: c for c in fiche.tous()}
        assert champs["siret"].valeur == "93520225900013"
        assert "portage" in champs["siret"].aide.lower()
        assert champs["statut"].valeur == "Portage salarial"

    def test_le_remplissage_automatique_suit(self, profile):
        from freelance_radar.web.bookmarklet import valeurs_profil

        profile.identity = {**profile.identity, "siret": ""}
        profile.portage = {"siret": "93520225900013"}
        v = valeurs_profil(profile)
        assert v["siret"] == "93520225900013"
        assert v["statut"] == "Portage salarial"
