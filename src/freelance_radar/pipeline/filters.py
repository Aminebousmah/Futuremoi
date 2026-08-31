"""Filtres et deduplication.

Chaque rejet est compte et motive : `radar scrape --explain` affiche le
tableau des raisons, ce qui evite de chercher a l'aveugle pourquoi une
campagne ne remonte rien.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import Config
from ..models import ContractType, JobOffer, RemotePolicy
from .enrich import count_core_skills
from .normalize import contains_any, normalize_key


@dataclass
class FilterReport:
    kept: list[JobOffer] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)
    duplicates: int = 0

    @property
    def total_rejected(self) -> int:
        return sum(self.rejected.values())

    def summary(self) -> str:
        if not self.rejected and not self.duplicates:
            return "aucun rejet"
        parts = [f"{reason}={n}" for reason, n in self.rejected.most_common()]
        if self.duplicates:
            parts.append(f"doublons={self.duplicates}")
        return ", ".join(parts)


_CONTRACT_ALIASES = {
    "freelance": ContractType.FREELANCE,
    "contractor": ContractType.FREELANCE,
    "independant": ContractType.FREELANCE,
    "mission": ContractType.FREELANCE,
    "portage": ContractType.FREELANCE,
    "cdi": ContractType.CDI,
    "cdd": ContractType.CDD,
}


def _wanted_contracts(cfg: Config) -> set[ContractType]:
    return {_CONTRACT_ALIASES.get(normalize_key(c), ContractType.UNKNOWN)
            for c in cfg.filters.contracts} - {ContractType.UNKNOWN}


def matches_keywords(offer: JobOffer, cfg: Config) -> bool:
    """Une offre est "Data" si son titre le dit, ou si le faisceau technique le prouve.

    Chercher un mot-cle n'importe ou dans la description laisse passer trop de
    bruit : "data" apparait dans quantite d'annonces marketing ou commerciales.
    On exige donc soit un mot-cle dans le titre, soit un nombre minimum de
    competences de la taxonomie ET un mot-cle dans le corps de l'annonce.
    """
    if contains_any(offer.title, cfg.search.keywords_any):
        return True
    # Porte de secours : on ne compte que les competences specifiquement data.
    # Compter toute la taxonomie laissait passer des postes de developpement
    # (Python + Java + Azure + CI/CD suffisaient), qui n'ont rien a faire ici.
    if count_core_skills(offer.skills) < cfg.search.min_skills_without_title_match:
        return False
    return contains_any(offer.description, cfg.search.keywords_any) is not None


def is_excluded(offer: JobOffer, cfg: Config) -> str | None:
    """Rend le terme d'exclusion trouve, en priorisant le titre (plus fiable)."""
    hit = contains_any(offer.title, cfg.search.exclude_any)
    if hit:
        return hit
    # Dans la description, on n'exclut que les mentions de contrat non voulues
    # (un mot comme "commercial" peut apparaitre sans que l'offre soit hors sujet).
    return contains_any(offer.description[:1500], ["stage", "alternance", "apprentissage"])


def apply_filters(offers: list[JobOffer], cfg: Config,
                  publiees_depuis: datetime | None = None) -> FilterReport:
    """Filtre une moisson.

    `publiees_depuis` limite l'examen aux annonces parues depuis cette date :
    c'est le mode incremental, qui evite de repasser sur tout le catalogue a
    chaque campagne. Une annonce sans date de publication est conservee — la
    deduplication se charge des doublons.
    """
    report = FilterReport()
    wanted = _wanted_contracts(cfg)
    seen_ids: set[str] = set()
    seen_cross: set[str] = set()

    for offer in offers:
        # --- doublons ---
        if offer.id in seen_ids or offer.cross_source_key() in seen_cross:
            report.duplicates += 1
            continue

        # --- pertinence ---
        if not matches_keywords(offer, cfg):
            report.rejected["hors mots-cles"] += 1
            continue
        excluded = is_excluded(offer, cfg)
        if excluded:
            report.rejected[f"terme exclu ({excluded})"] += 1
            continue

        # --- contrat ---
        if wanted and offer.contract != ContractType.UNKNOWN and offer.contract not in wanted:
            report.rejected[f"contrat {offer.contract.value}"] += 1
            continue

        # --- parue depuis le dernier passage ---
        if publiees_depuis and offer.published_at:
            parution = offer.published_at
            if parution.tzinfo is None:
                parution = parution.replace(tzinfo=timezone.utc)
            if parution < publiees_depuis:
                report.rejected["deja vue au passage precedent"] += 1
                continue

        # --- anciennete ---
        age = offer.age_days
        if age is not None and cfg.filters.max_age_days and age > cfg.filters.max_age_days:
            report.rejected["trop ancienne"] += 1
            continue

        # --- TJM ---
        rate = offer.daily_rate
        if rate is not None and cfg.filters.min_daily_rate and rate < cfg.filters.min_daily_rate:
            report.rejected["TJM trop bas"] += 1
            continue

        # --- localisation ---
        # L'exclusion passe avant la liste blanche : "Remote - Brazil only"
        # contient "remote" et passerait sinon la liste des localisations.
        blocked = contains_any(offer.location, cfg.filters.locations_exclude)
        if blocked:
            report.rejected[f"localisation exclue ({blocked})"] += 1
            continue

        # On teste le champ `location` seul, jamais le statut teletravail :
        # sinon toute offre full remote matcherait l'entree "remote" et le
        # filtre ne servirait plus a rien. Beaucoup d'annonces "remote" sont
        # en fait restreintes a un pays donne, et c'est ce qu'on veut filtrer.
        #
        # Certaines sources sont nationales par construction (France Travail) :
        # y appliquer une liste blanche de villes n'apporte rien et se heurte a
        # leur format ("31 - Toulouse" ne contient ni "France" ni "FR").
        skip_location = offer.source in cfg.filters.locations_skip_sources
        if (cfg.filters.locations and not skip_location
                and not contains_any(offer.location, cfg.filters.locations)):
            report.rejected["localisation"] += 1
            continue
        if cfg.filters.remote_only and offer.remote not in (
            RemotePolicy.FULL_REMOTE, RemotePolicy.HYBRID
        ):
            report.rejected["non remote"] += 1
            continue


        seen_ids.add(offer.id)
        seen_cross.add(offer.cross_source_key())
        report.kept.append(offer)

    return report
