"""Modeles de donnees partages par tout le pipeline."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Caracteres de controle : une source mal encodee en glisse jusque dans les
# titres, et ils font planter l'affichage console sous Windows.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class ContractType(str, Enum):
    FREELANCE = "freelance"
    CDI = "cdi"
    CDD = "cdd"
    STAGE = "stage"
    ALTERNANCE = "alternance"
    UNKNOWN = "unknown"


class RemotePolicy(str, Enum):
    FULL_REMOTE = "full_remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class ApplicationStatus(str, Enum):
    """Cycle de vie d'une candidature (utilise par `radar track`)."""

    NEW = "new"                 # offre retenue, rien de genere
    DRAFTED = "drafted"         # brouillon genere, en relecture
    SENT = "sent"               # envoyee manuellement par l'utilisateur
    REPLIED = "replied"         # reponse recue
    INTERVIEW = "interview"     # entretien planifie
    WON = "won"                 # mission signee
    REJECTED = "rejected"       # refus
    ARCHIVED = "archived"       # abandonnee


class JobOffer(BaseModel):
    """Une offre normalisee, quelle que soit sa source."""

    # --- Identite ---
    id: str = ""                       # empreinte stable, calculee si absente
    source: str                        # "remotive", "freework", ...
    source_id: str = ""                # identifiant natif chez la source
    url: str

    # --- Contenu ---
    title: str
    company: str = ""
    description: str = ""
    raw_html: str = ""

    # --- Attributs normalises ---
    location: str = ""
    remote: RemotePolicy = RemotePolicy.UNKNOWN
    contract: ContractType = ContractType.UNKNOWN
    daily_rate_min: int | None = None   # EUR / jour
    daily_rate_max: int | None = None
    # Extrait du texte ayant requalifie l'offre en freelance quand la source
    # ne l'avait pas declare (cf. pipeline.freelance). None = tag d'origine.
    freelance_marker: str | None = None
    duration_months: float | None = None
    start_date: date | None = None
    skills: list[str] = Field(default_factory=list)

    # --- Metadonnees ---
    published_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    score: float = 0.0
    # Detail du scoring. Valeurs numeriques pour les signaux ponderes, plus
    # deux entrees prefixees d'un underscore (_matched_skills,
    # _missing_skills) qui portent des listes : d'ou le type `Any`.
    score_detail: dict[str, Any] = Field(default_factory=dict)
    status: ApplicationStatus = ApplicationStatus.NEW

    # --- Annotations de l'utilisateur ---
    notes: str = ""
    starred: bool = False      # mise en avant manuelle
    discarded: bool = False    # ecartee a la main ; masquee, mais conservee

    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def _clean_text(cls, v: Any) -> str:
        if not v:
            return ""
        cleaned = _CTRL_RE.sub("", str(v))
        return re.sub(r"\s+", " ", cleaned).strip()

    def model_post_init(self, _ctx: Any) -> None:
        if not self.id:
            self.id = self.fingerprint()

    def fingerprint(self) -> str:
        """Empreinte de deduplication.

        Basee sur (titre normalise + entreprise + source) plutot que sur l'URL :
        les job boards changent souvent leurs URLs (tracking, slugs) alors que
        le couple titre/entreprise reste stable. La source est incluse pour ne
        pas ecraser deux annonces distinctes republiees par des agregateurs.
        """
        norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())  # noqa: E731
        seed = f"{norm(self.title)}|{norm(self.company)}|{self.source}"
        return hashlib.sha1(seed.encode()).hexdigest()[:16]

    def cross_source_key(self) -> str:
        """Cle de deduplication inter-sources (meme mission sur 2 job boards)."""
        norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())  # noqa: E731
        return hashlib.sha1(f"{norm(self.title)}|{norm(self.company)}".encode()).hexdigest()[:16]

    @property
    def age_days(self) -> float | None:
        if not self.published_at:
            return None
        published = self.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - published).total_seconds() / 86400

    @property
    def daily_rate(self) -> int | None:
        """TJM representatif : la borne haute si connue, sinon la borne basse."""
        return self.daily_rate_max or self.daily_rate_min

    def short(self) -> str:
        rate = f"{self.daily_rate}EUR" if self.daily_rate else "TJM n/c"
        return (f"[{self.score:.0f}] {self.title} — {self.company or '?'} "
                f"({rate}, {self.location or '?'})")


class Application(BaseModel):
    """Un brouillon de candidature attache a une offre."""

    offer_id: str
    status: ApplicationStatus = ApplicationStatus.DRAFTED
    subject: str = ""
    cover_letter: str = ""
    email_body: str = ""
    highlights: list[str] = Field(default_factory=list)   # arguments retenus
    gaps: list[str] = Field(default_factory=list)         # competences manquantes
    proposed_rate: int | None = None
    generator: str = "template"                            # "template" | "llm"
    file_path: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: datetime | None = None
    notes: str = ""
