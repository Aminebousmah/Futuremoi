"""Interface web locale.

Elle ne reimplemente rien : elle pilote exactement le meme moteur que la CLI
(`pipeline.runner`, `apply.generator`, `storage.Database`). La regle du projet
tient donc aussi ici : l'interface genere des brouillons, elle n'envoie rien.
"""

from .app import create_app

__all__ = ["create_app"]
