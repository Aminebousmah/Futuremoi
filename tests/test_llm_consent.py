"""Verrou de consentement : aucune requete facturee sans accord explicite.

Ces tests sont la garde-fou du portefeuille. Une cle presente dans `.env` ne
doit jamais suffire a declencher un appel : il faut un geste par generation.
"""

from __future__ import annotations

import pytest

from freelance_radar.apply.llm import DEFAULT_MODEL, LLMWriter


@pytest.fixture
def cle_presente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simule un `.env` complet : cle valide et SDK installe."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-factice")


# --------------------------------------------------------------------------- #
#  Le defaut est fermé
# --------------------------------------------------------------------------- #
def test_defaut_sans_consentement(cle_presente: None) -> None:
    """Meme avec la cle, l'ecrivain est inerte tant qu'on ne l'autorise pas."""
    writer = LLMWriter()
    assert writer.consent is False
    assert writer.available() is False


def test_cle_seule_ne_suffit_pas(cle_presente: None) -> None:
    assert LLMWriter(consent=False).available() is False


def test_write_refuse_sans_consentement(cle_presente: None, monkeypatch) -> None:
    """`write()` ne doit pas meme construire de client HTTP."""
    def interdit(self):  # pragma: no cover - ne doit jamais s'executer
        raise AssertionError("un client a ete instancie sans consentement")

    monkeypatch.setattr(LLMWriter, "_get_client", interdit)
    assert LLMWriter(consent=False).write("peu importe") is None


def test_write_refuse_meme_si_available_est_force(cle_presente: None, monkeypatch) -> None:
    """Le second verrou tient si `available()` est contourne."""
    monkeypatch.setattr(LLMWriter, "available", lambda self: True)

    def interdit(self):  # pragma: no cover - ne doit jamais s'executer
        raise AssertionError("un client a ete instancie sans consentement")

    monkeypatch.setattr(LLMWriter, "_get_client", interdit)
    assert LLMWriter(consent=False).write("peu importe") is None


# --------------------------------------------------------------------------- #
#  Motifs de blocage lisibles
# --------------------------------------------------------------------------- #
def test_motif_absence_de_consentement(cle_presente: None) -> None:
    motif = LLMWriter(consent=False).blocked_reason()
    assert motif and "consentement" in motif


def test_motif_cle_absente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    motif = LLMWriter(consent=True).blocked_reason()
    assert motif and "ANTHROPIC_API_KEY" in motif


def test_aucun_blocage_quand_tout_est_reuni(cle_presente: None) -> None:
    pytest.importorskip("anthropic")
    assert LLMWriter(consent=True).blocked_reason() is None


# --------------------------------------------------------------------------- #
#  Choix du modele
# --------------------------------------------------------------------------- #
def test_modele_par_defaut_economique(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une lettre de motivation ne justifie pas le modele le plus cher."""
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert LLMWriter().model == DEFAULT_MODEL
    assert "haiku" in DEFAULT_MODEL


def test_modele_surchargeable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    assert LLMWriter().model == "claude-sonnet-5"
