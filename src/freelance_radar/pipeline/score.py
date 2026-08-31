"""Scoring : a quel point une offre merite une candidature.

Le score est une moyenne ponderee des signaux CONNUS, ramenee sur 100. Le
detail par signal est conserve dans `offer.score_detail` pour que la CLI
puisse expliquer pourquoi une offre est classee haut ou bas (`radar show`).

POURQUOI LES SIGNAUX INCONNUS SONT EXCLUS
-----------------------------------------
Chaque signal valait auparavant 0.5 quand l'annonce ne disait rien : une
offre muette etait donc punie exactement comme une offre defavorable. Or la
plupart des annonces sont muettes, faute de place ou parce que la source
tronque son texte -- mesure du 31/08/2026 sur 150 offres en base :

    teletravail renseigne  13 %      duree  7 %      TJM  15 %

Trois signaux sur cinq restaient donc bloques a 0.5, soit 45 % du poids
total. Meme avec un recoupement de competences parfait, le score plafonnait
mecaniquement a 76 : le seuil de 65 devenait presque inatteignable des qu'un
autre signal faiblissait.

Un signal inconnu est desormais retire du numerateur ET du denominateur : on
note sur ce que l'on sait. La moyenne generale ne bouge pas (51.0 avant comme
apres), mais les offres au-dessus du seuil passent de 16 a 35 -- ce n'est pas
de l'inflation, c'est un plafond artificiel qui disparait.

Les signaux ecartes sont listes dans `score_detail["_signaux_ignores"]`, pour
qu'une offre bien notee faute d'information ne passe pas pour une certitude.
"""

from __future__ import annotations

from ..config import Config, Profile
from ..models import JobOffer, RemotePolicy
from .enrich import extract_skills
from .normalize import normalize_key

# Plafond applique quand l'annonce ne permet aucun recoupement de competences.
# Volontairement sous le seuil de candidature par defaut (65) : une offre dont
# on ignore le fond ne doit pas atterrir dans la pile "a traiter".
SCORE_SANS_COMPETENCES = 50.0


def _skills_score(offer: JobOffer,
                  profile: Profile) -> tuple[float | None, list[str], list[str]]:
    """Recouvrement competences offre / profil, pondere par le niveau declare.

    Rend (score 0-1 ou None si l'annonce ne cite aucune competence, communes,
    manquantes).
    """
    required = offer.skills or extract_skills(f"{offer.title} {offer.description}")
    if not required:
        return None, [], []  # annonce vague : signal absent, pas signal moyen

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


def _rate_score(offer: JobOffer, profile: Profile) -> float | None:
    """TJM affiche vs objectif. None si l'annonce n'affiche pas de TJM."""
    rate = offer.daily_rate
    target, floor = profile.rate_target, profile.rate_floor
    if not rate or not target:
        return None
    if rate >= target:
        return 1.0
    if floor and rate < floor:
        return 0.0
    span = target - (floor or target * 0.7)
    return max(0.0, min(1.0, (rate - (floor or target * 0.7)) / span)) if span else 0.5


def _remote_score(offer: JobOffer, profile: Profile) -> float | None:
    """None quand l'annonce ne dit rien de sa politique de teletravail."""
    if offer.remote is RemotePolicy.UNKNOWN:
        return None
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


def _freshness_score(offer: JobOffer, max_age_days: int) -> float | None:
    """Decroissance lineaire : une annonce du jour vaut 1, une annonce limite 0.

    None quand la source ne date pas son annonce.
    """
    age = offer.age_days
    if age is None:
        return None
    if age <= 1:
        return 1.0
    return max(0.0, 1.0 - (age / max(max_age_days, 1)))


def _duration_score(offer: JobOffer, profile: Profile) -> float | None:
    """None quand l'annonce n'annonce pas de duree de mission."""
    preferred = float(profile.constraints.get("preferred_duration_months", 6) or 6)
    if not offer.duration_months:
        return None
    ratio = offer.duration_months / preferred
    if ratio >= 1:
        return 1.0 if ratio <= 2.5 else 0.85   # tres longue mission : leger malus
    return max(0.2, ratio)


def score_offer(offer: JobOffer, profile: Profile, cfg: Config) -> JobOffer:
    """Calcule le score global et remplit `score_detail`."""
    weights = cfg.scoring.weights

    skills, matched, missing = _skills_score(offer, profile)
    signals: dict[str, float | None] = {
        "skills_match": skills,
        "daily_rate": _rate_score(offer, profile),
        "remote": _remote_score(offer, profile),
        "freshness": _freshness_score(offer, cfg.filters.max_age_days),
        "duration": _duration_score(offer, profile),
    }

    # Moyenne ponderee sur les seuls signaux renseignes (cf. en-tete du module).
    connus = {k: v for k, v in signals.items() if v is not None}
    poids_connus = sum(weights.get(k, 0) for k in connus)
    if poids_connus:
        score = sum(connus[k] * weights.get(k, 0) for k in connus) / poids_connus * 100
    else:
        # Annonce totalement muette : rien ne permet de la recommander.
        score = 0.0

    # Garde-fou. Noter sur ce qu'on sait devient absurde quand ce qu'on sait
    # ne dit rien du fond : une annonce dont seul le TJM est lisible obtenait
    # 100/100 parce que ce TJM etait bon. Le recoupement de competences est le
    # seul signal qui parle du poste ; sans lui, le score reste sous le seuil
    # de candidature au lieu de trôner en tete de liste.
    if skills is None:
        score = min(score, SCORE_SANS_COMPETENCES)

    ignores = [k for k, v in signals.items() if v is None]
    offer.score = round(score, 1)
    offer.score_detail = {
        **{k: (round(v, 3) if v is not None else None) for k, v in signals.items()},
        "_matched_skills": matched,      # conserve pour l'explication et la lettre
        "_missing_skills": missing,
        # Un score eleve appuye sur deux signaux ne vaut pas un score eleve
        # appuye sur cinq : la liste permet de le dire a l'ecran.
        "_signaux_ignores": ignores,
        "_poids_couvert": round(poids_connus / (sum(weights.values()) or 1.0), 3),
    }
    return offer


def rank(offers: list[JobOffer], profile: Profile, cfg: Config) -> list[JobOffer]:
    scored = [score_offer(o, profile, cfg) for o in offers]
    return sorted(scored, key=lambda o: o.score, reverse=True)
