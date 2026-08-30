"""Contrat commun a toutes les sources + registre de decouverte."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from typing import Any

from ..config import Config
from ..models import JobOffer
from .http import PoliteClient

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseScraper]] = {}


def register(cls: type[BaseScraper]) -> type[BaseScraper]:
    """Decorateur d'enregistrement : `@register` sur une sous-classe suffit."""
    _REGISTRY[cls.name] = cls
    return cls


def available_scrapers() -> dict[str, type[BaseScraper]]:
    return dict(_REGISTRY)


def build_scrapers(cfg: Config, client: PoliteClient,
                   only: Iterable[str] | None = None) -> list[BaseScraper]:
    """Instancie les scrapers actives dans la config (et filtres par `only`)."""
    wanted = set(only) if only else None
    out: list[BaseScraper] = []
    for name, source_cfg in cfg.enabled_sources().items():
        if wanted and name not in wanted:
            continue
        cls = _REGISTRY.get(name)
        if cls is None:
            log.warning("Source '%s' activee dans config.yaml mais sans implementation", name)
            continue
        scraper = cls(cfg, client, source_cfg)
        if not scraper.is_configured():
            log.warning("Source '%s' ignoree : %s", name, scraper.missing_requirement())
            continue
        out.append(scraper)
    if wanted:
        for name in wanted - {s.name for s in out}:
            log.warning("Source '%s' demandee mais non disponible/desactivee", name)
    return out


class BaseScraper(ABC):
    """Chaque source implemente `fetch()` et rend des `JobOffer` bruts.

    Le nettoyage (normalisation, filtres, scoring, dedup) est fait en aval par
    le pipeline : un scraper doit rester bete et se contenter d'extraire.
    """

    name: str = "base"
    label: str = "Source"
    homepage: str = ""

    # robots.txt regit l'exploration d'un site web. Il ne regit pas l'appel
    # d'une API documentee que l'on est autorise a consommer : les hotes d'API
    # servent frequemment un `Disallow: /` global (ils n'ont rien a indexer),
    # qui bloquerait a tort un client legitime sous contrat. Les sources `api`
    # passent donc ce drapeau a False ; les sources HTML le laissent a True.
    respects_robots: bool = True

    def __init__(self, cfg: Config, client: PoliteClient, source_cfg: dict[str, Any]):
        self.cfg = cfg
        self.client = client
        self.source_cfg = source_cfg or {}

    # -- capacites ---------------------------------------------------- #
    def is_configured(self) -> bool:
        """Faux si des credentials obligatoires manquent."""
        return True

    def missing_requirement(self) -> str:
        return "prerequis manquant"

    # -- extraction ---------------------------------------------------- #
    @abstractmethod
    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        """Rend les offres brutes correspondant aux mots-cles."""

    # -- utilitaires partages ------------------------------------------ #
    def run(self, keywords: list[str]) -> list[JobOffer]:
        """Execute la source en isolant les erreurs : une source HS n'arrete pas la campagne."""
        offers: list[JobOffer] = []
        try:
            for offer in self.fetch(keywords):
                offers.append(offer)
        except Exception as exc:
            log.error("Source %s en echec : %s", self.name, exc)
        log.info("Source %-16s -> %3d offres brutes", self.name, len(offers))
        return offers

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.source_cfg.get(key, default)

    def queries(self) -> list[str]:
        """Termes a envoyer a cette source.

        Par defaut ceux de `search.queries`, qu'une source peut surcharger via
        sa cle `queries` quand son vocabulaire differe (langue, taxonomie
        interne). Centraliser evite que chaque scraper invente ses propres
        termes, ce qui rendait la couverture reelle impossible a raisonner.
        """
        return list(self._cfg("queries") or self.cfg.search.queries or ["data"])

    # -- acces reseau : passent par le client poli, avec la politique de la source
    def get(self, url: str, **kwargs: Any) -> str:
        kwargs.setdefault("check_robots", self.respects_robots)
        return self.client.get(url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("check_robots", self.respects_robots)
        return self.client.get_json(url, **kwargs)
