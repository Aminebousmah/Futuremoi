"""Lettre de motivation générique, indépendante de l'offre.

Le générateur produit déjà une lettre par offre. Elle a un coût : il faut la
relire à chaque candidature, et une lettre sur-mesure faible vaut moins
qu'une lettre générique solide. Sur une plateforme freelance, la plupart des
formulaires attendent d'ailleurs un texte court et réutilisable.

D'où cette lettre-ci : écrite une fois à partir du profil, elle se copie
telle quelle et se retouche à la marge. Le CV, lui, reste adapté offre par
offre — c'est là que l'adaptation paie.

Tout ce qu'elle affirme vient de `profile.yaml` : positionnement,
références, contraintes. Rien n'est inventé, et aucun appel réseau n'est
fait.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Profile

# Deux références suffisent : au-delà, la lettre devient un CV en prose.
REFERENCES_CITEES = 2


MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")


def _date_lisible(valeur: object) -> str:
    """"2026-10-01" -> "1er octobre 2026". Une lettre ne s'écrit pas en ISO."""
    texte = str(valeur or "").strip()
    try:
        annee, mois, jour = (int(x) for x in texte.split("-"))
        premier = "1er" if jour == 1 else str(jour)
        return f"{premier} {MOIS[mois - 1]} {annee}"
    except (ValueError, IndexError):
        return texte


def _phrase_disponibilite(profile: Profile) -> str:
    contraintes = profile.constraints or {}
    morceaux = []
    if contraintes.get("available_from"):
        quand = _date_lisible(contraintes["available_from"])
        morceaux.append(f"disponible à partir du {quand}")
    if profile.rate_target:
        morceaux.append(f"TJM {profile.rate_target} € HT")
    rythme = contraintes.get("remote")
    jours = contraintes.get("max_onsite_days_per_week")
    if rythme == "hybrid" and jours:
        morceaux.append(f"hybride, jusqu'à {jours} jours sur site par semaine")
    elif rythme == "full_remote":
        morceaux.append("full remote")
    zones = contraintes.get("mobility") or []
    if zones:
        morceaux.append(", ".join(str(z) for z in zones))
    return " · ".join(morceaux)


def _preuves(profile: Profile) -> list[str]:
    lignes = []
    for ref in (profile.references or [])[:REFERENCES_CITEES]:
        client = ref.get("client", "")
        role = ref.get("role", "")
        fait = " ".join(str(ref.get("achievement", "")).split())
        entete = f"**{client}** — {role}" if role else f"**{client}**"
        lignes.append(f"- {entete}\n  {fait}" if fait else f"- {entete}")
    return lignes


def lettre_generique(profile: Profile) -> str:
    """Rend la lettre en Markdown, prête à copier."""
    ident = profile.identity or {}
    positionnement = profile.positioning or {}
    pitch = " ".join(str(positionnement.get("pitch", "")).split())

    contact = " · ".join(x for x in (
        ident.get("email", ""), ident.get("phone", ""), ident.get("website", ""),
    ) if x)

    lignes = [
        f"# {ident.get('full_name', '')}",
        f"*{ident.get('title', '')}*",
        "",
        contact,
        "",
        "---",
        "",
        "Bonjour,",
        "",
        pitch,
        "",
        "Ce que j'ai livré récemment :",
        "",
        *_preuves(profile),
        "",
        "Je cherche des missions où la donnée sert une décision : fiabiliser les "
        "chiffres, automatiser ce qui se répète, et donner aux directions des "
        "tableaux de bord sur lesquels elles s'engagent.",
        "",
        f"Conditions : {_phrase_disponibilite(profile)}.",
        "",
        "Mon CV détaille le parcours et la stack. Je reste disponible pour en "
        "parler de vive voix.",
        "",
        "Bien à vous,",
        f"{ident.get('full_name', '')}",
        "",
    ]
    return "\n".join(lignes)


def ecrire_lettre_generique(profile: Profile, destination: Path) -> Path:
    """Écrit la lettre et rend son chemin."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(lettre_generique(profile), encoding="utf-8")
    return destination
