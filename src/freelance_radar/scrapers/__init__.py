"""Sources d'offres. Importer ce module suffit a enregistrer tous les scrapers."""

# Les imports ci-dessous declenchent l'enregistrement via le decorateur @register.
from . import (
    adzuna,
    francetravail,
    freelanceinfo,
    freework,
    lesjeudis,
    remote_boards,
    remoteok,
    remotive,
)
from .base import BaseScraper, available_scrapers, build_scrapers, register
from .http import PoliteClient, RobotsDisallowed

__all__ = [
    "BaseScraper",
    "PoliteClient",
    "RobotsDisallowed",
    "available_scrapers",
    "build_scrapers",
    "register",
]
