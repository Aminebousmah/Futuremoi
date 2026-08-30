"""Generation de candidatures : lettre, email, suivi.

Regle du module : on genere des BROUILLONS. Aucun envoi automatique, aucune
soumission de formulaire. La relecture et l'envoi restent des gestes humains.
"""

from .generator import ApplicationGenerator

__all__ = ["ApplicationGenerator"]
