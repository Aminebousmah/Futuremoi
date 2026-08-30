"""Masquage des identifiants dans les journaux.

Plusieurs API (Adzuna) exigent leurs cles en parametres d'URL, et le client
HTTP journalise l'URL complete au niveau INFO. En mode verbeux, les cles se
retrouvaient donc en clair dans la console et dans les fichiers de log.

Le filtre ci-dessous s'applique a la racine du logging : il couvre aussi les
messages emis par httpx, uvicorn ou toute autre bibliotheque.
"""

from __future__ import annotations

import logging
import re

# Parametres d'URL ou de formulaire dont la valeur ne doit jamais etre journalisee.
_PARAMS_SENSIBLES = (
    "app_key", "app_id", "api_key", "apikey", "key",
    "client_secret", "client_id", "secret",
    "access_token", "token", "password",
)

_MOTIFS = [
    # cle=valeur dans une URL ou un corps de formulaire
    re.compile(rf"((?:{'|'.join(_PARAMS_SENSIBLES)})=)[^&\s\"']+", re.IGNORECASE),
    # en-tete Authorization
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    # cles Anthropic
    re.compile(r"(sk-ant-)[A-Za-z0-9._\-]+"),
]

MASQUE = "***"


def redact(texte: str) -> str:
    """Remplace toute valeur sensible par un masque."""
    for motif in _MOTIFS:
        texte = motif.sub(rf"\1{MASQUE}", texte)
    return texte


class RedactingFilter(logging.Filter):
    """Masque les identifiants dans un enregistrement de journal.

    On formate le message AVANT de masquer, puis on vide les arguments. Inspecter
    `record.args` un par un ne suffit pas : httpx y place un objet `httpx.URL`,
    pas une chaine, et l'URL — cles comprises — n'apparaissait qu'au formatage.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # un message mal forme ne doit pas casser le logging
            return True
        masque = redact(message)
        if masque != message:
            record.msg = masque
            record.args = ()
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Attache le filtre a la racine du logging, handlers compris.

    Les filtres poses sur un logger ne s'appliquent pas aux enregistrements
    remontes par ses enfants : on les pose donc aussi sur les handlers, par
    lesquels tout passe.
    """
    racine = logger or logging.getLogger()
    filtre = RedactingFilter()
    if not any(isinstance(f, RedactingFilter) for f in racine.filters):
        racine.addFilter(filtre)
    for handler in racine.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(filtre)
