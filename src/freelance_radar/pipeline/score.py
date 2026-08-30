"""Scoring : a quel point une offre merite une candidature.

Le score est une somme ponderee de 5 signaux, ramenee sur 100. Le detail par
signal est conserve dans `offer.score_detail` pour que la CLI puisse expliquer
pourquoi une offre est classee haut ou bas (`radar show <id>`).
"""

from __future__ import annotations

from ..config import Config, Profile
from ..models import JobOffer, RemotePolicy
from .enrich import extract_skills
from .normalize import normalize_key


def _skills_score(offer: JobOffer, profile: Profile) -> tuple[float, list[str], list[str]]:
    """Recouvrement competences offre / profil, pondere par le niveau declare.

    Rend (score 0-1, competences communes, competences manquantes).
    """
    required = offer.skills or extract_skills(f"{offer.title} {offer.description}")
    if not required:
        return 0.5, [], []  # annonce vague : score neutre, ni bonus ni penalite

    profile_skills = {normalize_key(s) for s in profile.all_skills}
    matched, missing, weight_sum = [], [], 0.0
    for skill in required:
        if normalize_key(skill) in profile_skills:
            matched.append(skill)
            weight_sum += profile.skill_weight(skill) or 0.6
        else:
            missing.append(skill)

    coverage = weight_sum / len(required)
    return min(coverage, 1.0), matched, missing


def _rate_score(offer: JobOffer, profile: Profile) -> float:
    """TJM affiche vs objectif. Une offre sans TJM reste neutre (0.5)."""
    rate = offer.daily_rate
    target, floor = profile.rate_target, profile.rate_floor
    if not rate or not target:
        return 0.5
    if rate >= target:
        return 1.0
    if floor and rate < floor:
        return 0.0
    span = target - (floor or target * 0.7)
    return max(0.0, min(1.0, (rate - (floor or target * 0.7)) / span)) if span else 0.5


def _remote_score(offer: JobOffer, profile: Profile) -> float:
    want = str(profile.constraints.get("remote", "hybrid")).lower()
    matrix = {
        "full_remote": {RemotePolicy.FULL_REMOTE: 1.0, RemotePolicy.HYBRID: 0.4,
                        RemotePolicy.ONSITE: 0.0, RemotePolicy.UNKNOWN: 0.5},
        "hybrid": {RemotePolicy.FULL_REMOTE: 1.0, RemotePolicy.HYBRID: 0.9,
                   RemotePolicy.ONSITE: 0.3, RemotePolicy.UNKNOWN: 0.5},
        "onsite": {RemotePolicy.FULL_REMOTE: 0.5, RemotePolicy.HYBRID: 0.8,
                   RemotePolicy.ONSITE: 1.0, RemotePolicy.UNKNOWN: 0.5},
    }
    return matrix.get(want, matrix["hybrid"]).get(offer.remote, 0.5)


def _freshness_score(offer: JobOffer, max_age_days: int) -> float:
    """Decroissance lineaire : une annonce du jour vaut 1, une annonce limite vaut 0."""
    age = offer.age_days
    if age is None:
        return 0.5
    if age <= 1:
        return 1.0
    return max(0.0, 1.0 - (age / max(max_age_days, 1)))


def _duration_score(offer: JobOffer, profile: Profile) -> float:
    preferred = float(profile.constraints.get("preferred_duration_months", 6) or 6)
    if not offer.duration_months:
        return 0.5
    ratio = offer.duration_months / preferred
    if ratio >= 1:
        return 1.0 if ratio <= 2.5 else 0.85   # tres longue mission : leger malus
    return max(0.2, ratio)


def score_offer(offer: JobOffer, profile: Profile, cfg: Config) -> JobOffer:
    """Calcule le score global et remplit `score_detail`."""
    weights = cfg.scoring.weights
    total_weight = sum(weights.values()) or 1.0

    skills, matched, missing = _skills_score(offer, profile)
    signals = {
        "skills_match": skills,
        "daily_rate": _rate_score(offer, profile),
        "remote": _remote_score(offer, profile),
        "freshness": _freshness_score(offer, cfg.filters.max_age_days),
        "duration": _duration_score(offer, profile),
    }

    score = sum(signals[k] * weights.get(k, 0) for k in signals) / total_weight * 100
    offer.score = round(score, 1)
    offer.score_detail = {
        **{k: round(v, 3) for k, v in signals.items()},
        "_matched_skills": matched,      # conserve pour l'explication et la lettre
        "_missing_skills": missing,
    }
    return offer


def rank(offers: list[JobOffer], profile: Profile, cfg: Config) -> list[JobOffer]:
    scored = [score_offer(o, profile, cfg) for o in offers]
    return sorted(scored, key=lambda o: o.score, reverse=True)
