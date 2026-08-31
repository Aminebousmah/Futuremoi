"""Detection du caractere freelance et du TJM dans le texte d'une annonce.

Beaucoup d'annonces reellement freelance sont mal etiquetees a la source.
France Travail en est le cas d'ecole : son referentiel `typeContrat` ne
propose `LIB` (profession liberale) que pour les vraies professions
liberales, si bien qu'une ESN qui cherche un prestataire poste en CDD, en
`MIS`, ou sans type de contrat du tout. La nature freelance n'apparait alors
que dans le corps de l'annonce -- typiquement sous la forme d'un TJM.

Ce module lit ce que la source n'a pas su declarer :

  * `extract_daily_rate()` rend la fourchette de TJM citee dans le texte ;
  * `is_freelance_text()` rend le marqueur qui prouve la nature freelance ;
  * `detect()` combine les deux en un `Signals` exploitable par l'enrichissement.

Le parti pris est d'etre conservateur : mieux vaut manquer une promotion que
faire remonter un CDI deguise. Un TJM plausible ou un marqueur explicite sont
exiges ; les mots ambigus ("mission", "regie") ne suffisent jamais seuls.

RENDEMENT MESURE (31/08/2026, 2052 annonces, 10 sources)
--------------------------------------------------------
Ce module rapporte peu, et il faut le savoir avant d'y investir :

  * TJM recuperes depuis le texte : 1 annonce. Les sources qui portent du
    freelance (Free-Work, Freelance-Info, Adzuna) livrent deja leur tarif en
    structure ; les job boards anglophones parlent en salaire annuel.
  * Requalifications de contrat : 0. Aucune source francaise ne cache de
    mission freelance derriere un tag salarie.

Une premiere version, plus permissive, en produisait 12 -- toutes fausses
(cf. la note sur `_FREELANCE_FORT`). Le module est conserve parce qu'il est
peu couteux et qu'il protege le jour ou une source changera de format, pas
parce qu'il debloque du volume aujourd'hui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bornes de plausibilite d'un TJM en EUR. En dessous de 100 on tombe sur des
# montants qui n'ont rien d'un tarif (nombre de jours, effectifs, references) ;
# au dessus de 2500 on attrape des budgets globaux ou des salaires annuels.
TJM_MIN_PLAUSIBLE = 100
TJM_MAX_PLAUSIBLE = 2500

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

# --- TJM -------------------------------------------------------------------
#
# Trois familles de formulations, de la plus explicite a la plus implicite.
# L'ordre compte : on s'arrete a la premiere qui donne un montant plausible.

_MOT_TJM = (r"(?:tjm|t\.j\.m\.?|tarif\s+journalier|taux\s+journalier"
            r"|prix\s+journalier|tarif\s+/?\s*jour)")
_DEVISE = r"(?:€|eur(?:os?)?\b|k?€)"
# Tirets et lettres accentues en echappements Unicode : `re` les interprete, et
# le fichier reste lisible en revue sans caracteres ambigus.
_SEP_FOURCHETTE = r"\s*(?:[-\u2013\u2014]|[àa]\s|et\s|/)\s*"

# Un montant, borne des deux cotes pour ne jamais tronquer un nombre plus long :
# sans `(?<!\d)` / `(?!\d)`, "15000" livrerait "1500", parfaitement plausible et
# parfaitement faux. La premiere alternative couvre le separateur de milliers
# ("1 500 €"), qui doit etre teste avant la forme compacte.
_NOMBRE = r"(?<!\d)(\d{1,2}[ \u00a0]\d{3}|\d{3,4})(?!\d)"

_PATTERNS_FOURCHETTE = (
    # "TJM : 450 - 550 €", "TJM entre 450 et 550"
    re.compile(
        rf"{_MOT_TJM}[^\d\n]{{0,30}}?{_NOMBRE}{_SEP_FOURCHETTE}{_NOMBRE}",
        re.IGNORECASE,
    ),
    # "450 à 550 € / jour", "450-550 euros par jour"
    re.compile(
        rf"{_NOMBRE}{_SEP_FOURCHETTE}{_NOMBRE}\s*{_DEVISE}?\s*(?:ht\s*)?"
        rf"(?:/|par\s+)\s*(?:j\b|jour)",
        re.IGNORECASE,
    ),
)

_PATTERNS_SIMPLE = (
    # "TJM : 500 €", "tarif journalier de 600"
    re.compile(rf"{_MOT_TJM}[^\d\n]{{0,30}}?{_NOMBRE}", re.IGNORECASE),
    # "500 € / jour", "500€/j", "600 euros par jour", "500 € HT par jour"
    re.compile(
        rf"{_NOMBRE}\s*{_DEVISE}?\s*(?:ht\s*)?(?:/|par\s+)\s*(?:j\b|jour)",
        re.IGNORECASE,
    ),
)

# Un TJM cite quelque part prouve la prestation independante : on ne parle pas
# de tarif journalier a un salarie.
_RX_MENTION_TJM = re.compile(_MOT_TJM, re.IGNORECASE)


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


def _plausible(value: int) -> bool:
    return TJM_MIN_PLAUSIBLE <= value <= TJM_MAX_PLAUSIBLE


def _montant(brut: str) -> int:
    """Convertit une capture en entier, separateur de milliers compris."""
    return int(brut.replace(" ", "").replace("\u00a0", ""))


def extract_daily_rate(text: str) -> tuple[int | None, int | None]:
    """Rend (min, max) du TJM cite dans le texte, chaque borne pouvant etre None.

    Une fourchette est preferee a un montant isole : "450 a 550" est plus
    informatif que "450". Les montants hors bornes de plausibilite sont
    ignores, ce qui ecarte les salaires annuels et les budgets de projet.
    """
    if not text:
        return None, None

    for rx in _PATTERNS_FOURCHETTE:
        for match in rx.finditer(text):
            low, high = _montant(match.group(1)), _montant(match.group(2))
            if low > high:
                low, high = high, low
            if _plausible(low) and _plausible(high):
                return low, high

    for rx in _PATTERNS_SIMPLE:
        for match in rx.finditer(text):
            value = _montant(match.group(1))
            if _plausible(value):
                return value, value

    return None, None


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
    match = _RX_MENTION_TJM.search(text)
    return match.group(0) if match else None


def detect(title: str, description: str) -> Signals:
    """Analyse une annonce et rend les signaux freelance/TJM qu'elle contient.

    Un CDI annonce dans le titre neutralise la detection : le texte peut bien
    contenir "prestation de services", l'employeur a ete explicite.
    """
    if _RX_CDI_TITRE.search(title or ""):
        return Signals()

    blob = f"{title}\n{description}"
    rate_min, rate_max = extract_daily_rate(blob)
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
