"""Detection du TJM et requalification en freelance."""

from __future__ import annotations

import pytest

from freelance_radar.models import ContractType, JobOffer
from freelance_radar.pipeline import freelance
from freelance_radar.pipeline.enrich import enrich_offer


# --------------------------------------------------------------------------- #
#  Extraction du TJM
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("texte, attendu", [
    ("TJM : 500 €", (500, 500)),
    ("TJM 450-550 EUR", (450, 550)),
    ("TJM entre 450 et 550 euros", (450, 550)),
    ("Tarif journalier de 600 € HT", (600, 600)),
    ("Taux journalier moyen : 480€", (480, 480)),
    ("Remuneration : 550 € / jour", (550, 550)),
    ("Budget 600€/j", (600, 600)),
    ("500 euros par jour", (500, 500)),
    ("Fourchette 400 a 650 € par jour", (400, 650)),
    # Les bornes inversees sont remises dans l'ordre.
    ("TJM 600 - 450", (450, 600)),
])
def test_extraction_tjm(texte: str, attendu: tuple[int, int]) -> None:
    assert freelance.extract_daily_rate(texte) == attendu


@pytest.mark.parametrize("texte", [
    "",
    "Salaire annuel : 45 000 € brut",
    "Equipe de 12 personnes, 3 ans d'experience",
    # Hors bornes de plausibilite : un TJM a 15 000 est un budget, pas un tarif.
    "TJM : 15000 €",
    "Ticket restaurant de 9 € par jour",
])
def test_pas_de_tjm_abusif(texte: str) -> None:
    """Un montant implausible ne doit jamais etre pris pour un TJM."""
    assert freelance.extract_daily_rate(texte) == (None, None)


@pytest.mark.parametrize("texte", [
    "TJM 1200 euros par jour",
    "TJM 1 200 euros par jour",          # espace fine
    "TJM 1\u00a0200 euros par jour",  # insecable, courante en typo FR
])
def test_separateur_de_milliers(texte: str) -> None:
    """Les trois ecritures d'un millier doivent donner le meme montant."""
    assert freelance.extract_daily_rate(texte) == (1200, 1200)


def test_fourchette_prioritaire_sur_montant_isole() -> None:
    """Une fourchette est plus informative qu'une borne seule."""
    assert freelance.extract_daily_rate("TJM 450-550 €, prime 200 € / jour") == (450, 550)


# --------------------------------------------------------------------------- #
#  Marqueurs freelance
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("texte", [
    "Mission en freelance",
    "Recherche consultant independant",
    "Ouvert aux travailleurs independants",
    "Possibilite de portage salarial",
    "Contrat de prestation a prevoir",
    "Statut auto-entrepreneur accepte",
    "Mission en independant, 6 mois",
])
def test_marqueurs_forts(texte: str) -> None:
    assert freelance.is_freelance_text(texte) is not None


@pytest.mark.parametrize("texte", [
    "Mission de 6 mois au sein de l'equipe",
    "Consultant BI en regie chez le client",
    "Poste a pourvoir rapidement",
])
def test_mots_ambigus_insuffisants(texte: str) -> None:
    """« mission », « regie », « consultant » existent aussi en CDI."""
    assert freelance.is_freelance_text(texte) is None


@pytest.mark.parametrize("texte", [
    # Releves sur des annonces France Travail salariees : chacun de ces termes
    # requalifiait a tort un CDD en freelance.
    "Vous travaillez de maniere independante sur vos dossiers",
    "Capacite a travailler en autonomie et de facon independante",
    "Gestion des sous-traitants du chantier",
    "Recours a la sous-traitance pour les pics d'activite",
    "Societe de prestation de services informatiques",
    "Reduction de la dependance aux prestataires externes",
])
def test_faux_positifs_ecartes(texte: str) -> None:
    """Ces formulations decrivent l'employeur ou une qualite, pas un statut."""
    assert freelance.is_freelance_text(texte) is None


def test_mention_tjm_vaut_marqueur() -> None:
    """On ne parle pas de tarif journalier a un salarie."""
    assert freelance.is_freelance_text("TJM a negocier selon profil") is not None


# --------------------------------------------------------------------------- #
#  Decision d'ensemble
# --------------------------------------------------------------------------- #
def test_cdi_dans_le_titre_neutralise() -> None:
    """Un CDI annonce est une information positive : le texte ne peut pas l'annuler."""
    signals = freelance.detect(
        "Data Analyst CDI",
        "Prestation de services au sein de l'equipe data, TJM 500 €",
    )
    assert signals.freelance is False
    assert signals.rate_min is None


def test_detection_complete() -> None:
    signals = freelance.detect(
        "Data Analyst Power BI",
        "Mission freelance de 6 mois. TJM 450-550 € selon experience.",
    )
    assert signals.freelance is True
    assert (signals.rate_min, signals.rate_max) == (450, 550)
    assert signals.has_rate is True


# --------------------------------------------------------------------------- #
#  Integration dans l'enrichissement
# --------------------------------------------------------------------------- #
def _offre(**kw) -> JobOffer:
    base = {
        "id": "test-1",
        "source": "francetravail",
        "url": "https://example.org/1",
        "title": "Data Analyst",
        "description": "",
    }
    return JobOffer(**{**base, **kw})


def test_requalification_depuis_cdd() -> None:
    """Le cas France Travail : une mission freelance postee en CDD."""
    offre = enrich_offer(_offre(
        contract=ContractType.CDD,
        description="Mission freelance, TJM 550 € HT par jour. Power BI, SQL.",
    ))
    assert offre.contract is ContractType.FREELANCE
    assert offre.daily_rate_max == 550
    assert offre.freelance_marker is not None


def test_requalification_depuis_contrat_inconnu() -> None:
    offre = enrich_offer(_offre(
        contract=ContractType.UNKNOWN,
        description="Recherche consultant independant pour un projet decisionnel.",
    ))
    assert offre.contract is ContractType.FREELANCE


def test_cdi_jamais_requalifie() -> None:
    """Un CDI declare par la source reste un CDI, quoi que dise le texte."""
    offre = enrich_offer(_offre(
        contract=ContractType.CDI,
        description="Mission freelance, TJM 550 €.",
    ))
    assert offre.contract is ContractType.CDI


def test_tjm_de_la_source_prioritaire() -> None:
    """Un TJM structure fourni par la source n'est pas ecrase par le texte."""
    offre = enrich_offer(_offre(
        contract=ContractType.FREELANCE,
        daily_rate_min=600, daily_rate_max=700,
        description="TJM 400 € pour un profil junior.",
    ))
    assert (offre.daily_rate_min, offre.daily_rate_max) == (600, 700)


def test_offre_sans_signal_inchangee() -> None:
    offre = enrich_offer(_offre(
        contract=ContractType.CDD,
        description="Poste de Data Analyst au sein de la direction financiere.",
    ))
    assert offre.contract is ContractType.CDD
    assert offre.freelance_marker is None
