"""Normalisation : texte, dates, TJM, type de contrat, teletravail, duree.

Ces fonctions sont pures et testables : elles ne dependent ni du reseau ni de la
base. Tous les scrapers s'appuient dessus pour produire des offres comparables.
Le module est volontairement en ASCII pur (les accents sont echappes) pour
eviter tout probleme d'encodage sous Windows.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, timezone

from dateutil import parser as date_parser

from ..models import ContractType, JobOffer, RemotePolicy

# --------------------------------------------------------------------------- #
#  Texte
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_NL_RE = re.compile(r"\n{3,}")
# Caracteres de controle C0/C1 : presents des qu'une source a mal encode
# son flux, et fatals a l'affichage console sous Windows.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Signature d'un texte UTF-8 relu en latin-1 ("donnÃ©es" au lieu de "donnees")
_MOJIBAKE_RE = re.compile(r"[\u00c2\u00c3\u00e2][\u0080-\u00bf]")


def fix_mojibake(text: str) -> str:
    """Repare un texte UTF-8 qui a ete decode en latin-1 par la source.

    Plusieurs job boards servent leurs descriptions dans cet etat. Le
    round-trip latin-1 -> utf-8 les restaure ; s'il echoue, on rend le
    texte d'origine plutot que de risquer une degradation.
    """
    if not text or not _MOJIBAKE_RE.search(text):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if _MOJIBAKE_RE.search(text) else text



def strip_html(raw: str) -> str:
    """HTML -> texte lisible, en preservant les sauts de paragraphe."""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    # Avant _WS_RE : la reparation du mojibake exige des octets 0xa0 intacts.
    text = fix_mojibake(text)
    text = _CTRL_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def deaccent(text: str) -> str:
    """Supprime les accents : indispensable pour matcher du francais saisi librement."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_key(text: str) -> str:
    return deaccent(text or "").lower().strip()


def contains_any(text: str, needles: list[str]) -> str | None:
    """Rend le premier terme trouve (sans accents, borne sur les sigles courts)."""
    hay = normalize_key(text)
    for needle in needles:
        n = normalize_key(needle)
        if not n:
            continue
        # Les sigles courts (bi, ml, etl) exigent une frontiere de mot pour
        # eviter les faux positifs du type "ambition" -> "bi".
        pattern = rf"\b{re.escape(n)}\b" if len(n) <= 3 else re.escape(n)
        if re.search(pattern, hay):
            return needle
    return None


# --------------------------------------------------------------------------- #
#  Dates
# --------------------------------------------------------------------------- #
def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = date_parser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_date(value: str | None) -> date | None:
    dt = parse_datetime(value)
    return dt.date() if dt else None


# --------------------------------------------------------------------------- #
#  TJM (taux journalier moyen)
# --------------------------------------------------------------------------- #
_EUR = r"(?:\u20ac|eur|euros?)"

# Bornes de plausibilite. En dessous de 200 on ramasse des tickets restaurant
# et des nombres de jours ; au dessus de 2500 des budgets ou des salaires.
TJM_MIN_PLAUSIBLE = 200
TJM_MAX_PLAUSIBLE = 2500

# Un montant, borne des deux cotes pour ne jamais tronquer un nombre plus long.
# Sans `(?<!\d)` / `(?!\d)`, "TJM : 15000 EUR" livrait "1500" -- plausible, et
# faux. La premiere alternative couvre le separateur de milliers ("1 200 EUR"),
# qui sans elle etait lu "200" : un TJM de 1200 tombait sous le seuil minimum
# et l'offre etait rejetee. Elle doit etre testee avant la forme compacte.
_NOMBRE = r"(?<!\d)(\d{1,2}[ \u00a0\u202f]\d{3}|\d{3,4})(?!\d)"

_MOT_TJM = (r"(?:tjm|t\.j\.m\.?|tarif\s+journalier|taux\s+journalier"
            r"|prix\s+journalier|daily\s+rate|tarif\s+/?\s*jour)")
_PAR_JOUR = r"(?:ht\s*)?(?:/|par\s+)?\s*(?:j\b|jours?|days?)"
_SEP_FOURCHETTE = r"\s*(?:[-\u2013\u2014]|\u00e0\s|a\s|et\s|to\s|/)\s*"

# Fourchettes d'abord : "450 a 550" est plus informatif que "450" seul.
_PATTERNS_FOURCHETTE = (
    # "TJM : 450 - 550 EUR", "TJM entre 450 et 550"
    re.compile(rf"{_MOT_TJM}[^\d\n]{{0,30}}?{_NOMBRE}{_SEP_FOURCHETTE}{_NOMBRE}",
               re.IGNORECASE),
    # "500-650 EUR / jour", "450 a 550 euros par jour"
    re.compile(rf"{_NOMBRE}\s*{_EUR}?\s*{_SEP_FOURCHETTE}{_NOMBRE}\s*{_EUR}?\s*{_PAR_JOUR}",
               re.IGNORECASE),
)

_PATTERNS_SIMPLE = (
    # "TJM : 500 EUR", "tarif journalier de 600"
    re.compile(rf"{_MOT_TJM}[^\d\n]{{0,30}}?{_NOMBRE}", re.IGNORECASE),
    # "550 EUR HT par jour", "600EUR/j"
    re.compile(rf"{_NOMBRE}\s*{_EUR}\s*{_PAR_JOUR}", re.IGNORECASE),
)


_RX_MENTION_TJM = re.compile(_MOT_TJM, re.IGNORECASE)


def mentions_daily_rate(text: str) -> str | None:
    """Rend l'expression qui annonce un tarif journalier, ou None.

    Utile au-dela de l'extraction du montant : une annonce qui parle de TJM
    s'adresse a un independant, meme quand elle ne chiffre rien.
    """
    if not text:
        return None
    match = _RX_MENTION_TJM.search(text)
    return match.group(0) if match else None


def _montant(brut: str) -> int:
    """Convertit une capture en entier, separateur de milliers compris."""
    return int(brut.replace(" ", "").replace("\u00a0", "").replace("\u202f", ""))


def _plausible(value: int) -> bool:
    return TJM_MIN_PLAUSIBLE <= value <= TJM_MAX_PLAUSIBLE


def parse_daily_rate(text: str) -> tuple[int | None, int | None]:
    """Extrait (min, max) du TJM en EUR depuis du texte libre.

    Ne rend une valeur que si elle est plausible (200-2500 EUR/j) : sinon on
    confondrait un TJM avec un code postal ou un salaire annuel.
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


# --------------------------------------------------------------------------- #
#  Contrat / teletravail / duree
# --------------------------------------------------------------------------- #
_FREELANCE_HINTS = [
    "freelance", "free-lance", "independant", "independent contractor", "contractor",
    "mission", "portage", "prestataire", "consultant externe", "sous-traitance",
]
_CDI_HINTS = ["cdi", "permanent", "full-time employee", "temps plein"]
_CDD_HINTS = ["cdd", "fixed-term", "interim"]
_STAGE_HINTS = ["stage", "internship", "stagiaire"]
_ALT_HINTS = ["alternance", "apprentissage", "apprenti", "contrat pro"]


def parse_contract(*texts: str) -> ContractType:
    blob = " ".join(t for t in texts if t)
    if contains_any(blob, _STAGE_HINTS):
        return ContractType.STAGE
    if contains_any(blob, _ALT_HINTS):
        return ContractType.ALTERNANCE
    if contains_any(blob, _FREELANCE_HINTS):
        return ContractType.FREELANCE
    if contains_any(blob, _CDD_HINTS):
        return ContractType.CDD
    if contains_any(blob, _CDI_HINTS):
        return ContractType.CDI
    return ContractType.UNKNOWN


_FULL_REMOTE = ["full remote", "100% remote", "fully remote", "remote only",
                "teletravail total", "100% teletravail", "anywhere", "worldwide"]
_HYBRID = ["hybride", "hybrid", "teletravail partiel", "remote partiel",
           "2 jours sur site", "3 jours sur site", "1 jour sur site"]
_REMOTE_WORD = ["remote", "teletravail", "distanciel", "a distance"]
_ONSITE = ["sur site", "on-site", "onsite", "presentiel", "no remote",
           "pas de teletravail"]


def parse_remote(*texts: str) -> RemotePolicy:
    blob = " ".join(t for t in texts if t)
    if contains_any(blob, _FULL_REMOTE):
        return RemotePolicy.FULL_REMOTE
    if contains_any(blob, _HYBRID):
        return RemotePolicy.HYBRID
    if contains_any(blob, _ONSITE):
        return RemotePolicy.ONSITE
    if contains_any(blob, _REMOTE_WORD):
        return RemotePolicy.HYBRID  # "remote" seul : par prudence, on suppose hybride
    return RemotePolicy.UNKNOWN


_DURATION_MONTHS = re.compile(r"(\d{1,2})\s*(?:\+\s*)?mois", re.IGNORECASE)
_DURATION_MONTHS_EN = re.compile(r"(\d{1,2})\s*(?:\+\s*)?months?", re.IGNORECASE)
_DURATION_YEARS = re.compile(r"(\d)\s*ans?\b", re.IGNORECASE)
_DURATION_WEEKS = re.compile(r"(\d{1,2})\s*semaines?", re.IGNORECASE)
_EXPERIENCE_RE = re.compile(
    r"\d{1,2}\s*(?:ans?|annees?|years?)\s*(?:d[e\u2019']\s*)?(?:experience|exp\b)",
    re.IGNORECASE,
)


def parse_duration_months(text: str) -> float | None:
    """Duree de mission en mois. Ecarte les mentions d'experience requise."""
    if not text:
        return None
    cleaned = _EXPERIENCE_RE.sub(" ", deaccent(text))
    for rx in (_DURATION_MONTHS, _DURATION_MONTHS_EN):
        m = rx.search(cleaned)
        if m:
            months = int(m.group(1))
            if 1 <= months <= 60:
                return float(months)
    m = _DURATION_WEEKS.search(cleaned)
    if m:
        return round(int(m.group(1)) / 4.33, 1)
    m = _DURATION_YEARS.search(cleaned)
    if m:
        years = int(m.group(1))
        if 1 <= years <= 5:
            return float(years * 12)
    return None


# --------------------------------------------------------------------------- #
#  Normalisation d'une offre complete
# --------------------------------------------------------------------------- #
def normalize_offer(offer: JobOffer) -> JobOffer:
    """Complete les champs manquants a partir du texte de l'annonce."""
    if offer.raw_html and not offer.description:
        offer.description = strip_html(offer.raw_html)
    elif "<" in offer.description:
        offer.description = strip_html(offer.description)

    blob = f"{offer.title}\n{offer.description}\n{offer.location}"

    if offer.contract == ContractType.UNKNOWN:
        offer.contract = parse_contract(blob)
    if offer.remote == RemotePolicy.UNKNOWN:
        offer.remote = parse_remote(blob)
    if offer.daily_rate_min is None and offer.daily_rate_max is None:
        offer.daily_rate_min, offer.daily_rate_max = parse_daily_rate(blob)
    if offer.duration_months is None:
        offer.duration_months = parse_duration_months(blob)

    offer.title = offer.title.strip()
    if not offer.id:
        offer.id = offer.fingerprint()
    return offer
