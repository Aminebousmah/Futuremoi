"""Detection du caractere freelance et du TJM dans le texte d'une annonce.

Beaucoup d'annonces reellement freelance sont mal etiquetees a la source.
France Travail en est le cas d'ecole : son referentiel `typeContrat` ne
propose `LIB` (profession liberale) que pour les vraies professions
liberales, si bien qu'une ESN qui cherche un prestataire poste en CDD, en
`MIS`, ou sans type de contrat du tout. La nature freelance n'apparait alors
que dans le corps de l'annonce -- typiquement sous la forme d'un TJM.

Ce module lit ce que la source n'a pas su declarer :

  * `is_freelance_text()` rend le marqueur qui prouve la nature freelance ;
  * `detect()` le combine au TJM (extrait par `normalize.parse_daily_rate`,
    seule implementation) en un `Signals` exploitable par l'enrichissement.

Le parti pris est d'etre conservateur : mieux vaut manquer une promotion que
faire remonter un CDI deguise. Un TJM plausible ou un marqueur explicite sont
exiges ; les mots ambigus ("mission", "regie") ne suffisent jamais seuls.

RENDEMENT MESURE (31/08/2026, 316 annonces France Travail)
----------------------------------------------------------
Elargir `type_contrat` de LIB a "LIB,CDD,MIS" fait passer la collecte de 20 a
316 annonces et n'en requalifie aucune : les 206 CDD sont de vrais CDD
salaries, sans TJM ni mention de statut. D'ou le retour a LIB.

Une premiere version, plus permissive, en requalifiait 12 -- toutes fausses
(cf. la note sur `_FREELANCE_FORT`). Le module est conserve parce qu'il est
peu couteux et qu'il protege le jour ou une source changera de format, pas
parce qu'il debloque du volume aujourd'hui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import mentions_daily_rate, parse_daily_rate

# Marqueurs qui, seuls, prouvent que la prestation est independante.
#
# Regle de tri : le marqueur doit qualifier le STATUT DU CANDIDAT. Tout ce qui
# peut decrire l'employeur, ses fournisseurs ou une maniere de travailler est
# exclu -- une mesure sur 316 annonces France Travail a montre que ces termes
# ne produisent que des faux positifs :
#   * "sous-traitance" / "sous-traitant" designent les fournisseurs du client
#     (releve sur des postes de technicien de maintenance et d'assistant
#     administratif, tous salaries) ;
#   * "independant" seul attrape "travailler de maniere independante", qui est
#     une qualite attendue d'un salarie ;
#   * "prestation de services" et "prestataire externe" servent aux ESN pour
#     se decrire elles-memes.
# D'ou l'exigence d'un contexte de statut autour de "independant".
_FREELANCE_FORT = (
    r"free-?lances?",
    r"freelancing",
    r"travailleu(?:r|se)s?\s+ind[ée]pendant",
    r"consultants?e?s?\s+ind[ée]pendant",
    r"prestataires?\s+ind[ée]pendant",
    r"statut\s+(?:d'?\s*)?ind[ée]pendant",
    r"\ben\s+ind[ée]pendant",
    r"portage\s+salarial",
    r"soci[ée]t[ée]\s+de\s+portage",
    r"auto-?entrepreneur",
    r"micro-?entreprise",
    r"contrat\s+de\s+prestation",
    r"\bsasu\b",
    r"\beurl\b",
)
_RX_FREELANCE = re.compile("|".join(_FREELANCE_FORT), re.IGNORECASE)

# Un CDI affiche noir sur blanc dans le titre interdit toute promotion :
# c'est le seul cas ou la source est plus fiable que le texte.
_RX_CDI_TITRE = re.compile(r"\bcdi\b|\bcontrat\s+[àa]\s+dur[ée]e\s+ind[ée]termin", re.IGNORECASE)


@dataclass(frozen=True)
class Signals:
    """Ce que le texte revele que la source n'a pas declare."""

    freelance: bool = False
    marker: str | None = None          # l'extrait qui a emporte la decision
    rate_min: int | None = None
    rate_max: int | None = None

    @property
    def has_rate(self) -> bool:
        return self.rate_min is not None or self.rate_max is not None


def is_freelance_text(text: str) -> str | None:
    """Rend le marqueur freelance trouve dans le texte, ou None.

    Le marqueur est rendu tel qu'il apparait pour que `--explain` puisse
    montrer sur quoi la decision s'est jouee.
    """
    if not text:
        return None
    match = _RX_FREELANCE.search(text)
    if match:
        return match.group(0)
    # Un TJM cite quelque part prouve la prestation independante : on ne
    # parle pas de tarif journalier a un salarie.
    return mentions_daily_rate(text)


def detect(title: str, description: str) -> Signals:
    """Analyse une annonce et rend les signaux freelance/TJM qu'elle contient.

    Un CDI annonce dans le titre neutralise la detection : le texte peut bien
    contenir "prestation de services", l'employeur a ete explicite.
    """
    if _RX_CDI_TITRE.search(title or ""):
        return Signals()

    blob = f"{title}\n{description}"
    rate_min, rate_max = parse_daily_rate(blob)
    marker = is_freelance_text(blob)

    # Une promotion doit toujours pouvoir s'expliquer. Quand c'est le tarif seul
    # qui l'a declenchee -- "550 € / jour" ne contient pas le mot "TJM" -- on
    # fabrique le motif, sinon la requalification serait silencieuse.
    if marker is None and rate_min is not None:
        marker = (f"TJM {rate_min} EUR" if rate_min == rate_max
                  else f"TJM {rate_min}-{rate_max} EUR")

    return Signals(
        freelance=marker is not None,
        marker=marker,
        rate_min=rate_min,
        rate_max=rate_max,
    )
