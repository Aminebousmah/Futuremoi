"""Remotive : API JSON publique, sans authentification.

Endpoint documente : https://remotive.com/api/remote-jobs
Bon signal pour les missions "contract" 100% remote, majoritairement en anglais.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from ..models import ContractType, JobOffer, RemotePolicy
from ..pipeline.normalize import parse_datetime, strip_html
from .base import BaseScraper, register

API_URL = "https://remotive.com/api/remote-jobs"

# job_type renvoye par l'API -> notre taxonomie
_JOB_TYPE = {
    "contract": ContractType.FREELANCE,
    "freelance": ContractType.FREELANCE,
    "full_time": ContractType.CDI,
    "part_time": ContractType.CDI,
    "internship": ContractType.STAGE,
    "temporary": ContractType.CDD,
}


@register
class RemotiveScraper(BaseScraper):
    name = "remotive"
    label = "Remotive (remote worldwide)"
    homepage = "https://remotive.com"
    respects_robots = False  # API JSON publique et documentee

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        limit = int(self._cfg("limit", 100))
        # L'API ne gere qu'un terme de recherche : on interroge les mots-cles
        # les plus discriminants un par un, la deduplication se fait en aval.
        queries = self._cfg("queries") or self._default_queries(keywords)
        for query in queries:
            payload = self.get_json(API_URL, params={"search": query, "limit": limit})
            for raw in payload.get("jobs", []):
                offer = self._parse(raw)
                if offer:
                    yield offer

    @staticmethod
    def _default_queries(keywords: list[str]) -> list[str]:
        """Remotive indexe surtout de l'anglais : on garde 3 requetes utiles."""
        base = ["data", "analytics", "machine learning"]
        extra = [k for k in keywords if k.lower() in ("etl", "business intelligence")]
        return base + extra[:1]

    def _parse(self, raw: dict) -> JobOffer | None:
        title = raw.get("title")
        if not title:
            return None

        description_html = raw.get("description", "") or ""
        location = raw.get("candidate_required_location", "") or ""

        # `tags` arrive parfois serialise en repr Python ("['aws', 'sql']")
        tags = raw.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = ast.literal_eval(tags)
            except (ValueError, SyntaxError):
                tags = [t.strip() for t in tags.strip("[]").split(",") if t.strip()]

        return JobOffer(
            source=self.name,
            source_id=str(raw.get("id", "")),
            url=raw.get("url", ""),
            title=title,
            company=raw.get("company_name", ""),
            description=strip_html(description_html),
            raw_html=description_html,
            location=location or "Remote",
            remote=RemotePolicy.FULL_REMOTE,  # Remotive ne publie que du remote
            contract=_JOB_TYPE.get(str(raw.get("job_type", "")).lower(), ContractType.UNKNOWN),
            skills=[str(t) for t in tags][:25],
            published_at=parse_datetime(raw.get("publication_date")),
        )
