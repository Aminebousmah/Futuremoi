"""Generation de candidatures : lettre, email, suivi.

Regle du module : on genere des BROUILLONS. Aucun envoi automatique, aucune
soumission de formulaire. La relecture et l'envoi restent des gestes humains.
"""

from .candidature import Champ, Fiche
from .entretien import FicheEntretien, construire_fiche, rendre_markdown
from .generator import ApplicationGenerator

__all__ = [
    "ApplicationGenerator",
    "Champ",
    "Fiche",
    "FicheEntretien",
    "construire_fiche",
    "rendre_markdown",
]
