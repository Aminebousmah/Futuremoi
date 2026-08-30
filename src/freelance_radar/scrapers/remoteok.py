"""Remote OK : API JSON publique, sans authentification.

Conditions d'utilisation de l'API (rappel) : Remote OK demande d'etre cite
comme source et de conserver un lien vers l'annonce d'origine. C'est le cas
ici : chaque offre garde son `url`, et le rapport HTML y renvoie directement.
"""

from __future__ import annotations

import ast
import html as html_module
from collections.abc import Iterator

from ..models import ContractType, JobOffer, RemotePolicy
from ..pipeline.normalize import parse_datetime, strip_html
from .base import BaseScraper, register

API_URL = "https://remoteok.com/api"


@register
class RemoteOkScraper(BaseScraper):
    name = "remoteok"
    label = "Remote OK (API publique)"
    homepage = "https://remoteok.com"
    respects_robots = False  # API JSON publique et documentee

    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        tags = self._cfg("tags") or ["data", "analyst", "sql", "machine learning"]
        for tag in tags:
            payload = self.get_json(API_URL, params={"tags": tag})
            if not isinstance(payload, list):
                continue
            # Le premier element du tableau est la notice legale, pas une offre.
            for raw in payload:
                if not isinstance(raw, dict) or "position" not in raw:
                    continue
                offer = self._parse(raw)
                if offer:
                    yield offer

    def _parse(self, raw: dict) -> JobOffer | None:
        title = raw.get("position")
        if not title:
            return None

        tags = raw.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = ast.literal_eval(tags)
            except (ValueError, SyntaxError):
                tags = [t.strip(" '\"") for t in tags.strip("[]").split(",") if t.strip()]

        description_html = raw.get("description", "") or ""
        return JobOffer(
            source=self.name,
            source_id=str(raw.get("id", "")),
            url=raw.get("url") or raw.get("apply_url", ""),
            title=html_module.unescape(title),
            company=html_module.unescape(str(raw.get("company", ""))),
            description=strip_html(description_html),
            raw_html=description_html,
            location=raw.get("location") or "Remote",
            remote=RemotePolicy.FULL_REMOTE,
            contract=ContractType.UNKNOWN,   # non expose par l'API, deduit du texte
            skills=[str(t) for t in tags][:25],
            published_at=parse_datetime(raw.get("date")),
        )
