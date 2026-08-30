"""Filtres : c'est ici que se joue le rapport signal/bruit de l'outil."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_offer

from freelance_radar.models import ContractType, RemotePolicy
from freelance_radar.pipeline.filters import apply_filters, is_excluded, matches_keywords


class TestKeywords:
    def test_titre_suffit(self, cfg):
        assert matches_keywords(make_offer(title="Data Engineer", skills=[]), cfg)

    def test_mot_cle_isole_dans_la_description_ne_suffit_pas(self, cfg):
        # Le piege classique : "data" cite en passant dans une annonce marketing
        offre = make_offer(
            title="Head of Marketing",
            description="Vous piloterez la strategie, en vous appuyant sur la data.",
            skills=[],
        )
        assert not matches_keywords(offre, cfg)

    def test_faisceau_technique_rattrape_un_titre_muet(self, cfg):
        # Il faut au moins deux competences specifiquement data : dbt seul,
        # entoure de Python et SQL (partages avec le dev), ne suffit pas.
        maigre = make_offer(
            title="Consultant technique senior",
            description="Pipeline data : Python, SQL, dbt.",
            skills=["Python", "SQL", "dbt"],
        )
        assert not matches_keywords(maigre, cfg)

        solide = make_offer(
            title="Consultant technique senior",
            description="Pipeline data : dbt sur Snowflake, orchestration Airflow.",
            skills=["Python", "dbt", "Snowflake", "Airflow"],
        )
        assert matches_keywords(solide, cfg)


class TestExclusions:
    def test_data_entry_est_exclu(self, cfg):
        assert is_excluded(make_offer(title="Data Entry Assistant"), cfg) == "data entry"

    def test_stage_dans_la_description(self, cfg):
        offre = make_offer(title="Data Analyst",
                           description="Offre de stage de 6 mois en data analyse.")
        assert is_excluded(offre, cfg) is not None

    def test_offre_saine_non_exclue(self, cfg):
        assert is_excluded(make_offer(), cfg) is None


class TestPipelineDeFiltrage:
    def test_garde_une_offre_conforme(self, cfg):
        rapport = apply_filters([make_offer()], cfg)
        assert len(rapport.kept) == 1 and rapport.total_rejected == 0

    def test_rejette_le_cdi(self, cfg):
        rapport = apply_filters([make_offer(contract=ContractType.CDI)], cfg)
        assert not rapport.kept and "contrat cdi" in rapport.rejected

    def test_garde_le_contrat_inconnu(self, cfg):
        # Beaucoup d'annonces n'affichent pas le type de contrat : les jeter
        # ferait perdre de vraies missions.
        rapport = apply_filters([make_offer(contract=ContractType.UNKNOWN)], cfg)
        assert len(rapport.kept) == 1

    def test_rejette_une_offre_trop_ancienne(self, cfg):
        vieille = make_offer(published_at=datetime.now(timezone.utc) - timedelta(days=90))
        assert "trop ancienne" in apply_filters([vieille], cfg).rejected

    def test_rejette_un_tjm_sous_le_plancher(self, cfg):
        assert "TJM trop bas" in apply_filters(
            [make_offer(daily_rate_min=250, daily_rate_max=300)], cfg).rejected

    def test_garde_une_offre_sans_tjm(self, cfg):
        # Un TJM absent n'est pas un TJM bas : la majorite des annonces n'en affichent pas.
        rapport = apply_filters([make_offer(daily_rate_min=None, daily_rate_max=None)], cfg)
        assert len(rapport.kept) == 1

    def test_rejette_une_localisation_hors_perimetre(self, cfg):
        assert "localisation" in apply_filters(
            [make_offer(location="Kuala Lumpur, Malaysia")], cfg).rejected

    def test_le_full_remote_ne_court_circuite_pas_la_localisation(self, cfg):
        # "Remote - Brazil only" contient "remote" : seule la liste d'exclusion,
        # evaluee en premier, permet de l'ecarter.
        offre = make_offer(location="Remote - Brazil only", remote=RemotePolicy.FULL_REMOTE)
        motifs = apply_filters([offre], cfg).rejected
        assert any(m.startswith("localisation exclue") for m in motifs)

    def test_le_full_remote_reellement_ouvert_est_conserve(self, cfg):
        offre = make_offer(location="Remote - France", remote=RemotePolicy.FULL_REMOTE)
        assert len(apply_filters([offre], cfg).kept) == 1

    def test_deduplique_dans_une_meme_campagne(self, cfg):
        rapport = apply_filters([make_offer(), make_offer()], cfg)
        assert len(rapport.kept) == 1 and rapport.duplicates == 1

    def test_deduplique_entre_sources(self, cfg):
        # Meme mission republiee par un agregateur : une seule doit rester.
        a = make_offer(source="freework")
        b = make_offer(source="adzuna", url="https://autre.example/offre")
        rapport = apply_filters([a, b], cfg)
        assert len(rapport.kept) == 1 and rapport.duplicates == 1


class TestFormatsDeLocalisation:
    """Les sources n'ecrivent pas les lieux de la meme facon.

    France Travail rend "92 - Nanterre" : ni la region, ni le nom du
    departement n'apparaissent. Sans les codes dans la liste blanche, des
    missions franciliennes valides etaient silencieusement rejetees.
    """

    def test_code_departement_reconnu(self, cfg):
        cfg.filters.locations = ["paris", "92"]
        offre = make_offer(location="92 - Nanterre")
        assert len(apply_filters([offre], cfg).kept) == 1

    def test_le_code_exige_une_frontiere_de_mot(self, cfg):
        # "92" ne doit pas matcher dans "1992" ni dans un numero de rue.
        cfg.filters.locations = ["92"]
        offre = make_offer(location="Bordeaux, depuis 1992")
        assert "localisation" in apply_filters([offre], cfg).rejected

    def test_format_nomme_toujours_reconnu(self, cfg):
        cfg.filters.locations = ["ile-de-france", "92"]
        offre = make_offer(location="Montreuil, Ile-de-France, FR")
        assert len(apply_filters([offre], cfg).kept) == 1


class TestCouvertureNationale:
    def test_suffixe_pays_fr_reconnu(self, cfg):
        # Free-Work rend "Lyon, Auvergne-Rhone-Alpes, FR" : le mot "France"
        # n'y figure pas, seul le suffixe pays permet de la retenir.
        cfg.filters.locations = ["france", "fr", "remote"]
        offre = make_offer(location="Lyon, Auvergne-Rhone-Alpes, FR")
        assert len(apply_filters([offre], cfg).kept) == 1

    def test_fr_n_est_pas_matche_dans_un_autre_mot(self, cfg):
        cfg.filters.locations = ["fr"]
        offre = make_offer(location="Frankfurt, Germany")
        assert "localisation" in apply_filters([offre], cfg).rejected

    def test_source_nationale_dispensee_du_filtre(self, cfg):
        # France Travail ne publie que des offres francaises : lui appliquer une
        # liste blanche de villes ne fait que perdre des missions.
        cfg.filters.locations = ["paris"]
        cfg.filters.locations_skip_sources = ["francetravail"]
        offre = make_offer(source="francetravail", location="31 - Toulouse")
        assert len(apply_filters([offre], cfg).kept) == 1

    def test_les_autres_sources_restent_filtrees(self, cfg):
        cfg.filters.locations = ["paris"]
        cfg.filters.locations_skip_sources = ["francetravail"]
        offre = make_offer(source="remoteok", location="Kuala Lumpur")
        assert "localisation" in apply_filters([offre], cfg).rejected


class TestExclusionFormateurs:
    def test_formateur_exclu_sur_le_titre(self, cfg):
        cfg.search.exclude_any = ["formateur"]
        assert is_excluded(make_offer(title="Formateur en architecture Data"), cfg)

    def test_formation_citee_dans_la_description_n_exclut_pas(self, cfg):
        # Le terme n'est teste que sur le titre : une mission qui mentionne
        # "formation continue" dans son texte doit rester eligible.
        cfg.search.exclude_any = ["formateur", "formation"]
        offre = make_offer(title="Data Engineer Senior",
                           description="Mission data. Formation continue des equipes metier.")
        assert is_excluded(offre, cfg) is None


class TestCompetencesSpecifiquementData:
    """Un radar Data ne doit pas remonter des postes de developpement.

    Le piege : Python, Java, Docker, CI/CD et AWS sont partages entre data et
    developpement logiciel. Les compter dans la porte de secours faisait passer
    des offres de dev des lors que "data" apparaissait quelque part dans le texte.
    """

    def test_outils_partages_ne_suffisent_pas(self, cfg):
        offre = make_offer(
            title="Senior Software Engineer C#/.NET",
            description="Equipe produit, migration cloud. Traitement de data en base.",
            skills=["Python", "Java", "Docker", "CI/CD", "AWS"],
        )
        assert not matches_keywords(offre, cfg)

    def test_competences_data_ouvrent_la_porte(self, cfg):
        offre = make_offer(
            title="Consultant technique senior",
            description="Refonte de la plateforme data du groupe.",
            skills=["dbt", "Airflow", "Python"],
        )
        assert matches_keywords(offre, cfg)

    def test_le_titre_reste_prioritaire(self, cfg):
        # Un titre explicite passe meme sans aucune competence detectee.
        assert matches_keywords(make_offer(title="Data Analyst", skills=[]), cfg)


class TestMetiersVoisins:
    @pytest.mark.parametrize("titre", [
        "Senior QA Engineer",
        "Developpeur Full Stack Java/React",
        "Ingenieur DevOps - Socle technique",
        "Software Engineer Backend",
    ])
    def test_metiers_hors_perimetre_exclus(self, cfg, titre):
        cfg.search.exclude_any = ["qa engineer", "full stack", "devops",
                                  "software engineer", "backend"]
        assert is_excluded(make_offer(title=titre), cfg)

    def test_data_engineer_non_impacte(self, cfg):
        cfg.search.exclude_any = ["qa engineer", "full stack", "devops",
                                  "software engineer", "backend"]
        assert is_excluded(make_offer(title="Data Engineer Senior"), cfg) is None


class TestTermesDeRecherche:
    """`queries` (ce qu'on demande) et `keywords_any` (ce qu'on garde) sont distincts."""

    def test_une_source_herite_des_termes_globaux(self, cfg):
        from freelance_radar.scrapers.base import BaseScraper

        class Muette(BaseScraper):
            name = "muette"

            def fetch(self, keywords):
                return iter(())

        cfg.search.queries = ["data analyst", "power bi"]
        assert Muette(cfg, client=None, source_cfg={}).queries() == ["data analyst", "power bi"]

    def test_une_source_peut_surcharger(self, cfg):
        from freelance_radar.scrapers.base import BaseScraper

        class Anglophone(BaseScraper):
            name = "anglophone"

            def fetch(self, keywords):
                return iter(())

        cfg.search.queries = ["decisionnel"]
        source = Anglophone(cfg, client=None, source_cfg={"queries": ["analytics"]})
        assert source.queries() == ["analytics"]

    def test_un_intitule_bi_passe_le_filtre_de_titre(self, cfg):
        # Sans le vocabulaire BI dans keywords_any, ces titres tombaient dans la
        # porte de secours et pouvaient etre rejetes faute de competences data.
        cfg.search.keywords_any = ["data", "power bi", "decisionnel", "data analyst"]
        for titre in ["Consultant Power BI senior",
                      "Chef de projet decisionnel",
                      "Data Analyst confirme"]:
            assert matches_keywords(make_offer(title=titre, skills=[]), cfg), titre


class TestSourcesNationalesEtFormatsDeLieu:
    """Les agregateurs francais n'ecrivent ni "France" ni "FR" dans leurs lieux.

    Adzuna rend "8eme Arrondissement, Paris" ou "Merignac, Bordeaux". Comme il
    est interroge avec country=fr, tous ses resultats sont francais : lui
    appliquer la liste blanche rejetait 3 offres sur 4.
    """

    @pytest.mark.parametrize("lieu", [
        "8eme Arrondissement, Paris",
        "Merignac, Bordeaux",
        "Herault, Occitanie",
        "Villeurbanne, Lyon",
    ])
    def test_lieux_francais_sans_marqueur_pays(self, cfg, lieu):
        cfg.filters.locations = ["france", "fr", "remote"]
        cfg.filters.locations_skip_sources = ["adzuna"]
        assert len(apply_filters([make_offer(source="adzuna", location=lieu)], cfg).kept) == 1

    def test_une_source_non_listee_reste_filtree(self, cfg):
        cfg.filters.locations = ["france", "fr", "remote"]
        cfg.filters.locations_skip_sources = ["adzuna"]
        offre = make_offer(source="remoteok", location="Merignac, Bordeaux")
        assert "localisation" in apply_filters([offre], cfg).rejected
