"""Recomposition du parcours en fonction de l'offre.

Deux expériences, deux projets, cinq puces chacun : l'ordre par défaut est
chronologique, donc muet sur ce que l'annonce cherche. Un recruteur lit les
deux premières lignes de chaque bloc — autant que ce soient les bonnes.

Ce que fait ce module
---------------------
  * il classe les puces d'une expérience par pertinence pour l'offre ;
  * il classe les projets entre eux, pour que le plus proche passe devant ;
  * il choisit, parmi les formulations déclarées d'une même puce, celle qui
    parle le vocabulaire de l'annonce ;
  * il fait remonter les puces optionnelles quand l'annonce les réclame.

Ce qu'il ne fait pas, et ne fera pas
------------------------------------
Il n'écrit aucune puce. Une compétence exigée que le parcours ne porte pas
reste un écart : `apply.entretien` la nomme pour qu'elle soit préparée, au
lieu de la maquiller. Un CV qui revendique un outil jamais pratiqué se casse
au premier cas pratique, et coûte plus cher que la mission qu'il visait.

Le rapprochement est automatique : les outils cités dans une puce sont
reconnus par la même table que le reste (`cv.ALIAS`), donc « dashboards
(SAP BI4, Power BI) » ressort sur une offre Power BI sans rien déclarer.

Variantes de formulation
------------------------
Une même mission se raconte de plusieurs façons vraies. « Consolidation des
données et harmonisation du suivi » et « harmonisation des référentiels et
définitions partagées des indicateurs » décrivent le même travail ; la
seconde parle à une annonce qui demande de la gouvernance. Déclarer ces
formulations sous `variantes` laisse le module choisir celle qui recoupe
l'offre.

La règle est stricte : une variante doit décrire **le même travail réel**,
avec les mêmes chiffres. Elle change le vocabulaire, jamais les faits. Une
variante qui ajouterait un outil non pratiqué serait une invention, avec le
coût que cela suppose au premier cas pratique.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Profile
from ..models import JobOffer
from ..pipeline.normalize import normalize_key
from .cv import _competences, _motifs, classer_competences

# Une puce optionnelle n'apparaît que si l'offre la réclame vraiment : sans
# seuil, elle remonterait sur n'importe quelle annonce et le CV s'allongerait
# sans gagner en pertinence.
SEUIL_OPTIONNELLE = 2.0

# Plafond de puces affichées par bloc, optionnelles comprises. Au-delà, le CV
# déborde et les premières lignes perdent leur poids.
PUCES_MAX = 6


@dataclass
class Puce:
    texte: str
    score: float = 0.0
    outils: list[str] = field(default_factory=list)
    optionnelle: bool = False
    reformulee: bool = False      # une variante a ete preferee a l'originale


@dataclass
class BlocParcours:
    """Une expérience ou un projet, ses puces déjà ordonnées."""

    titre: str
    sous_titre: str = ""
    periode: str = ""
    lieu: str = ""
    puces: list[Puce] = field(default_factory=list)
    score: float = 0.0


def _texte(puce: object) -> str:
    """Une puce est soit une chaîne, soit un dict `{texte: ...}`."""
    if isinstance(puce, dict):
        return str(puce.get("texte", ""))
    return str(puce)


def competences_citees(texte: str, profile: Profile) -> list[str]:
    """Compétences de l'inventaire réellement nommées dans la puce."""
    hay = normalize_key(texte)
    trouvees: list[str] = []
    for competence in (c for liste in _competences(profile).values() for c in liste):
        for motif in _motifs(competence):
            borne = rf"\b{re.escape(motif)}\b" if len(motif) <= 4 else re.escape(motif)
            if re.search(borne, hay):
                trouvees.append(competence)
                break
    return trouvees


def _scores_offre(offer: JobOffer, profile: Profile) -> dict[str, float]:
    """Ce que l'offre réclame, par compétence."""
    return {competence: score
            for _categorie, competence, score in classer_competences(offer, profile)}


def _noter_texte(texte: str, profile: Profile,
                 demande: dict[str, float]) -> tuple[float, list[str]]:
    outils = competences_citees(texte, profile)
    # Somme plutôt que maximum : une puce qui cite trois outils demandés vaut
    # mieux qu'une qui n'en cite qu'un, même très demandé.
    return sum(max(demande.get(o, 0.0), 0.0) for o in outils), outils


def _variantes(brut: object) -> list[str]:
    if not isinstance(brut, dict):
        return []
    return [_texte(v) for v in (brut.get("variantes") or [])]


def _noter(brut: object, profile: Profile, demande: dict[str, float]) -> Puce:
    """Retient la formulation qui recoupe le mieux l'offre.

    À égalité, la formulation d'origine gagne : on ne réécrit pas un CV pour
    un gain nul, et la version par défaut est celle que l'auteur a relue.
    """
    base = _texte(brut)
    meilleur_score, meilleurs_outils = _noter_texte(base, profile, demande)
    meilleur_texte, reformule = base, False

    for variante in _variantes(brut):
        score, outils = _noter_texte(variante, profile, demande)
        if score > meilleur_score:
            meilleur_score, meilleurs_outils = score, outils
            meilleur_texte, reformule = variante, True

    return Puce(texte=meilleur_texte, score=meilleur_score,
                outils=meilleurs_outils, reformulee=reformule)


def _ordonner(puces: list[Puce]) -> list[Puce]:
    """Tri stable : à score égal, l'ordre d'origine est conservé.

    Sans stabilité, deux générations successives sur la même offre pourraient
    rendre des CV différents, ce qui rend la relecture pénible.
    """
    return sorted(puces, key=lambda p: -p.score)


def _composer_bloc(brut: dict, profile: Profile, demande: dict[str, float],
                   *, titre_cle: str, sous_titre_cle: str) -> BlocParcours:
    puces = [_noter(p, profile, demande) for p in (brut.get("puces") or [])]

    # Les optionnelles ne rejoignent le CV que sur les offres qui les appellent.
    for brute in brut.get("puces_optionnelles") or []:
        puce = _noter(brute, profile, demande)
        puce.optionnelle = True
        if puce.score >= SEUIL_OPTIONNELLE:
            puces.append(puce)

    ordonnees = _ordonner(puces)[:PUCES_MAX]
    return BlocParcours(
        titre=str(brut.get(titre_cle, "")),
        sous_titre=str(brut.get(sous_titre_cle, "")),
        periode=str(brut.get("periode", "")),
        lieu=str(brut.get("lieu", "")),
        puces=ordonnees,
        score=sum(p.score for p in ordonnees),
    )


def composer_experiences(offer: JobOffer, profile: Profile) -> list[BlocParcours]:
    """Expériences dans l'ordre chronologique, puces réordonnées.

    L'ordre des postes ne bouge pas : un CV se lit du plus récent au plus
    ancien, et bousculer cette convention pour un gain de pertinence
    ferait douter de la chronologie.
    """
    demande = _scores_offre(offer, profile)
    brutes = (profile.cv or {}).get("parcours", {}).get("experiences") or []
    return [_composer_bloc(e, profile, demande,
                           titre_cle="poste", sous_titre_cle="client")
            for e in brutes]


def composer_projets(offer: JobOffer, profile: Profile) -> list[BlocParcours]:
    """Projets classés par proximité avec l'offre, puces réordonnées.

    Les projets, eux, n'ont pas de chronologie qui tienne : le plus proche
    de la mission passe devant.
    """
    demande = _scores_offre(offer, profile)
    brutes = (profile.cv or {}).get("parcours", {}).get("projets") or []
    blocs = [_composer_bloc(p, profile, demande,
                            titre_cle="nom", sous_titre_cle="sous_titre")
             for p in brutes]
    return sorted(blocs, key=lambda b: -b.score)


def resume_adaptation(experiences: list[BlocParcours],
                      projets: list[BlocParcours]) -> list[str]:
    """Ce qui a bougé, pour que la note de relecture le dise."""
    lignes: list[str] = []
    for bloc in [*experiences, *projets]:
        mises_en_avant = [p for p in bloc.puces if p.score > 0]
        if not mises_en_avant:
            continue
        premiere = mises_en_avant[0]
        outils = ", ".join(premiere.outils) or "—"
        marque = " *(puce optionnelle, remontée pour cette offre)*" \
            if premiere.optionnelle else ""
        lignes.append(f"- **{bloc.titre}** — mise en avant : {outils}{marque}")
    return lignes
