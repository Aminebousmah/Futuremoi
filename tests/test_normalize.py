"""Parsing du texte des annonces : la source de la majorite des faux positifs."""

from __future__ import annotations

import pytest

from freelance_radar.models import ContractType, RemotePolicy
from freelance_radar.pipeline.normalize import (
    contains_any,
    deaccent,
    fix_mojibake,
    parse_contract,
    parse_daily_rate,
    parse_duration_months,
    parse_remote,
    strip_html,
)


class TestStripHtml:
    def test_conserve_les_paragraphes(self):
        assert strip_html("<p>Un</p><p>Deux</p>") == "Un\nDeux"

    def test_supprime_script_et_style(self):
        out = strip_html("<div>Texte<script>alert(1)</script><style>p{}</style></div>")
        assert "alert" not in out and "p{}" not in out

    def test_supprime_les_caracteres_de_controle(self):
        assert "\x88" not in strip_html("<p>Data\x88 Engineer</p>")

    def test_decode_les_entites(self):
        assert strip_html("<p>R&amp;D &eacute;quipe</p>") == "R&D \u00e9quipe"


class TestMojibake:
    def test_repare_un_utf8_lu_en_latin1(self):
        assert fix_mojibake("donn\u00c3\u00a9es") == "donn\u00e9es"

    def test_laisse_un_texte_sain_intact(self):
        assert fix_mojibake("donn\u00e9es \u00e0 Paris") == "donn\u00e9es \u00e0 Paris"

    def test_ne_degrade_pas_si_la_reparation_echoue(self):
        texte = "co\u00fbt \u20ac 100"
        assert fix_mojibake(texte) == texte


class TestDailyRate:
    @pytest.mark.parametrize("texte,attendu", [
        ("TJM : 600\u20ac/j", (600, 600)),
        ("Tarif 500-650 \u20ac / jour", (500, 650)),
        ("taux journalier de 750", (750, 750)),
        ("550 EUR HT par jour", (550, 550)),
    ])
    def test_extrait_le_tjm(self, texte, attendu):
        assert parse_daily_rate(texte) == attendu

    @pytest.mark.parametrize("texte", [
        "Code postal 75015",           # pas un TJM
        "Salaire 45000 euros par an",  # annuel, pas journalier
        "Equipe de 12 personnes",
        "",
    ])
    def test_ignore_les_nombres_non_pertinents(self, texte):
        assert parse_daily_rate(texte) == (None, None)


class TestDuration:
    def test_mois(self):
        assert parse_duration_months("Mission de 6 mois renouvelable") == 6.0

    def test_annees_converties(self):
        assert parse_duration_months("Duree de mission : 3 ans") == 36.0

    def test_ignore_l_experience_requise(self):
        # "5 ans d'experience" ne doit pas devenir une duree de mission
        assert parse_duration_months("Profil avec 5 ans d'experience requis") is None

    def test_semaines(self):
        assert parse_duration_months("Mission de 8 semaines") == pytest.approx(1.8, abs=0.1)


class TestContractEtRemote:
    @pytest.mark.parametrize("texte,attendu", [
        ("Mission freelance longue duree", ContractType.FREELANCE),
        ("Contrat en CDI", ContractType.CDI),
        ("Stage de fin d'etudes", ContractType.STAGE),
        ("Contrat en alternance", ContractType.ALTERNANCE),
        ("Poste sans precision", ContractType.UNKNOWN),
    ])
    def test_contrat(self, texte, attendu):
        assert parse_contract(texte) == attendu

    def test_stage_prime_sur_freelance(self):
        # Une annonce de stage citant "mission" ne doit pas passer pour freelance
        assert parse_contract("Stage : mission data de 6 mois") == ContractType.STAGE

    @pytest.mark.parametrize("texte,attendu", [
        ("Poste 100% remote", RemotePolicy.FULL_REMOTE),
        ("Teletravail partiel, 2 jours sur site", RemotePolicy.HYBRID),
        ("Presence sur site exigee", RemotePolicy.ONSITE),
        ("Rien de precis", RemotePolicy.UNKNOWN),
    ])
    def test_remote(self, texte, attendu):
        assert parse_remote(texte) == attendu


class TestMatching:
    def test_ignore_les_accents(self):
        assert deaccent("donn\u00e9es \u00e0 g\u00e9rer") == "donnees a gerer"
        assert contains_any("Ing\u00e9nieur Donn\u00e9es", ["donnees"]) == "donnees"

    def test_les_sigles_courts_exigent_une_frontiere_de_mot(self):
        # "bi" ne doit pas matcher dans "ambition"
        assert contains_any("Forte ambition", ["bi"]) is None
        assert contains_any("Consultant BI senior", ["bi"]) == "bi"
