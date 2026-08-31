"""Detection du TJM et requalification en freelance."""

from __future__ import annotations

import pytest

from freelance_radar.models import ContractType, JobOffer
from freelance_radar.pipeline import freelance
from freelance_radar.pipeline.enrich import enrich_offer


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
