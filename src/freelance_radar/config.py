"""Chargement de la configuration (config.yaml + profile.yaml + .env)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

try:  # python-dotenv est optionnel a l'execution
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a: Any, **_kw: Any) -> bool:
        return False


def project_root() -> Path:
    """Racine du projet : trois niveaux au-dessus de ce fichier."""
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
#  Schemas de configuration
# --------------------------------------------------------------------------- #
class SearchConfig(BaseModel):
    # `queries` = ce qu'on DEMANDE aux sources (leur moteur de recherche).
    # `keywords_any` = ce qu'on GARDE une fois les resultats revenus.
    # Les deux sont distincts : on interroge avec le vocabulaire du metier vise,
    # on filtre avec un panier plus large pour ne pas jeter une bonne annonce
    # au libelle inattendu.
    queries: list[str] = Field(default_factory=lambda: ["data"])
    keywords_any: list[str] = Field(default_factory=lambda: ["data"])
    exclude_any: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["fr", "en"])
    # Si le titre ne contient aucun mot-cle, nombre de competences
    # techniques exigees pour retenir l'offre malgre tout.
    min_skills_without_title_match: int = 3


class FiltersConfig(BaseModel):
    contracts: list[str] = Field(default_factory=lambda: ["freelance"])
    max_age_days: int = 30
    min_daily_rate: int = 0
    locations: list[str] = Field(default_factory=list)
    locations_exclude: list[str] = Field(default_factory=list)
    locations_skip_sources: list[str] = Field(default_factory=list)
    remote_only: bool = False
    # N'examiner que les annonces parues depuis le dernier passage. Une marge
    # rattrape les sources qui indexent avec du retard.
    only_since_last_run: bool = True
    refresh_margin_days: int = 2


class HttpConfig(BaseModel):
    delay_seconds: float = 1.5
    timeout_seconds: float = 20
    max_retries: int = 3
    respect_robots_txt: bool = True
    cache_ttl_minutes: int = 60


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "skills_match": 45,
            "daily_rate": 20,
            "remote": 15,
            "freshness": 10,
            "duration": 10,
        }
    )
    apply_threshold: float = 65


class ApplicationConfig(BaseModel):
    auto_send: bool = False          # verrouille a False, cf. guard() ci-dessous
    language: str = "fr"
    tone: str = "professionnel"
    max_words: int = 320
    output_dir: str = "output/applications"
    use_llm: bool = True


class StorageConfig(BaseModel):
    database: str = "data/radar.db"
    cache_dir: str = "data/cache"


class Config(BaseModel):
    search: SearchConfig = Field(default_factory=SearchConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    http: HttpConfig = Field(default_factory=HttpConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    application: ApplicationConfig = Field(default_factory=ApplicationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    # --- chemins absolus derives ---
    @property
    def db_path(self) -> Path:
        return _abs(self.storage.database)

    @property
    def cache_path(self) -> Path:
        return _abs(self.storage.cache_dir)

    @property
    def applications_path(self) -> Path:
        return _abs(self.application.output_dir)

    def enabled_sources(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.sources.items() if v.get("enabled", False)}


def _abs(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else project_root() / path


# --------------------------------------------------------------------------- #
#  Profil freelance
# --------------------------------------------------------------------------- #
class Profile(BaseModel):
    identity: dict[str, Any] = Field(default_factory=dict)
    positioning: dict[str, Any] = Field(default_factory=dict)
    skills: dict[str, list[str]] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(default_factory=list)
    documents: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    # Inventaire d'outils et rubriques fixes du CV, utilises pour composer
    # la section competences en fonction de l'offre.
    cv: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.identity.get("full_name", "")

    @property
    def all_skills(self) -> list[str]:
        out: list[str] = []
        for level in ("expert", "advanced", "familiar"):
            out.extend(self.skills.get(level, []) or [])
        return out

    def skill_weight(self, skill: str) -> float:
        """Poids d'une competence selon le niveau declare."""
        s = skill.lower()
        for level, weight in (("expert", 1.0), ("advanced", 0.75), ("familiar", 0.45)):
            if any(s == k.lower() for k in (self.skills.get(level) or [])):
                return weight
        return 0.0

    @property
    def rate_target(self) -> int:
        return int(self.constraints.get("daily_rate_target", 0) or 0)

    @property
    def rate_floor(self) -> int:
        return int(self.constraints.get("daily_rate_floor", 0) or 0)


# --------------------------------------------------------------------------- #
#  Chargement
# --------------------------------------------------------------------------- #
def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(project_root() / ".env")
    cfg_path = Path(path) if path else project_root() / "config" / "config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Configuration introuvable : {cfg_path}")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)


def load_profile(path: str | Path | None = None) -> Profile:
    """Charge config/profile.yaml, avec repli sur profile.example.yaml."""
    if path:
        p = Path(path)
    else:
        p = project_root() / "config" / "profile.yaml"
        if not p.exists():
            p = project_root() / "config" / "profile.example.yaml"
    if not p.exists():
        raise FileNotFoundError(
            "Aucun profil trouve. Copiez config/profile.example.yaml "
            "vers config/profile.yaml puis completez-le."
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)


def profile_is_customized() -> bool:
    return (project_root() / "config" / "profile.yaml").exists()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def user_agent() -> str:
    return env(
        "RADAR_USER_AGENT",
        "freelance-radar/0.1 (veille freelance personnelle; contact via le site source)",
    )
