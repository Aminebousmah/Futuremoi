"""Fiche de candidature : tout ce qu'un formulaire d'employeur reclame.

Les formulaires de candidature posent presque toujours les memes questions —
identite, contact, disponibilite, pretentions — puis deux ou trois questions
ouvertes propres au poste. Ce module rassemble les reponses au meme endroit,
pretes a etre copiees champ par champ.

Il ne soumet rien : il prepare. L'envoi reste un geste humain, comme pour la
lettre et l'email.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config import Profile
from ..models import JobOffer, RemotePolicy


@dataclass
class Champ:
    """Une reponse prete a coller dans un formulaire."""

    cle: str            # identifiant stable, utilise par le remplissage automatique
    libelle: str        # l'intitule tel qu'un formulaire le pose
    valeur: str
    aide: str = ""      # ce qu'il faut verifier avant de coller


@dataclass
class Fiche:
    identite: list[Champ] = field(default_factory=list)
    mission: list[Champ] = field(default_factory=list)
    questions: list[Champ] = field(default_factory=list)

    def tous(self) -> list[Champ]:
        return [*self.identite, *self.mission, *self.questions]

    def par_cle(self) -> dict[str, str]:
        return {c.cle: c.valeur for c in self.tous() if c.valeur}


def _nom_prenom(complet: str) -> tuple[str, str]:
    """Separe prenom et nom. Convention francaise : le prenom vient en premier."""
    morceaux = complet.split()
    if len(morceaux) < 2:
        return complet, ""
    return morceaux[0], " ".join(morceaux[1:])


def _experience_sur(offer: JobOffer, profile: Profile, competence: str) -> str:
    """Une phrase de preuve sur une competence, tiree des references reelles."""
    for ref in profile.references:
        stack = [str(s) for s in (ref.get("stack") or [])]
        if any(competence.lower() in s.lower() or s.lower() in competence.lower()
               for s in stack):
            acquis = " ".join(str(ref.get("achievement", "")).split())
            return (f"Chez {ref.get('client')} ({ref.get('period')}), "
                    f"sur {', '.join(stack)} : {acquis}")
    annees = profile.positioning.get("years_experience", 3)
    return (f"{annees} ans de pratique en environnement de production, "
            f"dans le public et le prive.")


# Parametres de suivi ajoutes par les agregateurs : ils portent notre
# identifiant d'application et n'ont rien a faire dans un lien envoye a un
# client.
_TRACKING = {"utm_medium", "utm_source", "utm_campaign", "utm_term",
             "utm_content", "gclid", "fbclid", "ref", "referrer"}


def url_propre(url: str) -> str:
    """Retire les parametres de suivi d'une URL d'annonce."""
    if not url:
        return ""
    parts = urlsplit(url)
    gardes = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k.lower() not in _TRACKING]
    return urlunsplit(parts._replace(query=urlencode(gardes)))


_RYTHME = {
    RemotePolicy.FULL_REMOTE: "full remote",
    RemotePolicy.HYBRID: "hybride",
    RemotePolicy.ONSITE: "sur site",
    RemotePolicy.UNKNOWN: "à convenir",
}


def construire_fiche(offer: JobOffer, profile: Profile, *,
                     tjm_propose: int, disponibilite: str,
                     accroche: str, message: str) -> Fiche:
    """Assemble la fiche pour une offre.

    Les elements redactionnels (accroche, message) viennent du generateur de
    candidature : la fiche ne les reecrit pas, elle les met a portee de copie.
    """
    ident = profile.identity or {}
    prenom, nom = _nom_prenom(str(ident.get("full_name", "")))
    contraintes = profile.constraints or {}

    identite = [
        Champ("prenom", "Prénom", prenom),
        Champ("nom", "Nom", nom),
        Champ("nom_complet", "Nom complet", str(ident.get("full_name", ""))),
        Champ("email", "E-mail", str(ident.get("email", ""))),
        Champ("telephone", "Téléphone", str(ident.get("phone", ""))),
        Champ("linkedin", "LinkedIn", str(ident.get("linkedin", ""))),
        Champ("github", "GitHub", str(ident.get("github", ""))),
        Champ("site", "Site / portfolio", str(ident.get("website", ""))),
        Champ("ville", "Ville", str(ident.get("city", ""))),
        Champ("titre", "Intitulé de poste", str(ident.get("title", ""))),
        Champ("statut", "Statut", profile.statut_juridique),
        # En portage, le SIRET attendu est celui de la société : c'est elle
        # qui contracte et facture. L'aide le dit, pour qu'un numéro emprunté
        # ne soit jamais recopié comme une immatriculation personnelle.
        Champ("siret", "SIRET", profile.siret,
              aide=("SIRET de la société de portage"
                    if not (profile.identity or {}).get("siret")
                    else "votre immatriculation")),
    ]

    experience = profile.positioning.get("years_experience", "")
    mission = [
        Champ("tjm", "TJM / prétentions", f"{tjm_propose} € HT / jour"),
        Champ("disponibilite", "Disponibilité", disponibilite),
        Champ("annees_experience", "Années d'expérience", str(experience)),
        Champ("mobilite", "Mobilité",
              ", ".join(str(m) for m in (contraintes.get("mobility") or []))),
        Champ("rythme", "Rythme de travail souhaité",
              f"{_RYTHME.get(offer.remote, 'à convenir')} — "
              f"jusqu'à {contraintes.get('max_onsite_days_per_week', 3)} jours sur site"),
        Champ("reference_offre", "Référence de l'offre", url_propre(offer.url)),
    ]

    # Les competences que l'offre demande et que le profil couvre : c'est sur
    # celles-la qu'un formulaire posera une question d'experience.
    communes = (offer.score_detail or {}).get("_matched_skills") or []
    principale = communes[0] if communes else (offer.skills[0] if offer.skills else "")

    questions = [
        Champ("pourquoi", "Pourquoi cette mission ?", accroche),
        Champ("message", "Message / lettre de motivation courte", message),
    ]
    if principale:
        questions.append(Champ(
            f"experience_{principale.lower().replace(' ', '_')}",
            f"Votre expérience sur {principale}",
            _experience_sur(offer, profile, principale),
        ))
    if communes:
        questions.append(Champ(
            "competences_cles", "Compétences clés pour ce poste",
            ", ".join(communes[:6]),
        ))
    return Fiche(identite=identite, mission=mission, questions=questions)
