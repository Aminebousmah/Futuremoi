"""Extraction schema.org JobPosting depuis du HTML.

La majorite des job boards exposent un bloc <script type="application/ld+json">
decrivant l'offre (SEO Google for Jobs). C'est bien plus stable que des
selecteurs CSS : c'est donc la strategie par defaut pour toutes les sources HTML,
les selecteurs ne servant que de repli.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator
from typing import Any

from ..models import ContractType, JobOffer
from ..pipeline.normalize import parse_datetime, strip_html

log = logging.getLogger(__name__)

_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_EMPLOYMENT_MAP = {
    "CONTRACTOR": ContractType.FREELANCE,
    "CONTRACT": ContractType.FREELANCE,
    "TEMPORARY": ContractType.CDD,
    "FULL_TIME": ContractType.CDI,
    "PART_TIME": ContractType.CDI,
    "INTERN": ContractType.STAGE,
    "INTERNSHIP": ContractType.STAGE,
    "APPRENTICESHIP": ContractType.ALTERNANCE,
}


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    """Parcourt tout l'arbre JSON et rend chaque objet rencontre.

    Un parcours generique plutot qu'une liste de conteneurs connus : les pages
    de resultats emboitent les offres de facons variees (`@graph`, mais aussi
    `ItemList` -> `itemListElement` -> `ListItem` -> `item`). Chercher partout
    evite d'avoir a deviner la structure de chaque site.
    """
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)


def iter_ld_blocks(html_text: str) -> Iterator[dict[str, Any]]:
    """Rend chaque objet JSON-LD de la page, a n'importe quel niveau d'imbrication."""
    for raw in _LD_RE.findall(html_text or ""):
        try:
            # strict=False : plusieurs job boards laissent passer des tabulations
            # et des sauts de ligne bruts dans leurs chaines, ce que le JSON
            # standard interdit. Refuser tout le bloc pour un caractere de mise
            # en page ferait perdre des dizaines d'offres valides.
            data = json.loads(raw.strip(), strict=False)
        except json.JSONDecodeError as exc:
            log.debug("bloc JSON-LD illisible : %s", exc)
            continue
        yield from _walk(data)


def _is_job_posting(node: dict[str, Any]) -> bool:
    types = node.get("@type")
    types = [types] if isinstance(types, str) else (types or [])
    return any(str(t).lower() == "jobposting" for t in types)


def find_job_posting(html_text: str) -> dict[str, Any] | None:
    """Premiere offre de la page — pour une page de detail."""
    return next((n for n in iter_ld_blocks(html_text) if _is_job_posting(n)), None)


def find_all_job_postings(html_text: str) -> list[dict[str, Any]]:
    """Toutes les offres de la page — pour une page de resultats qui les embarque.

    Certains sites publient l'integralite de leurs annonces dans le JSON-LD de
    la page de resultats : une seule requete suffit alors pour des dizaines
    d'offres, au lieu d'une par annonce.
    """
    vues: set[str] = set()
    offres = []
    for node in iter_ld_blocks(html_text):
        if not _is_job_posting(node):
            continue
        cle = str(node.get("url") or node.get("@id") or node.get("title") or id(node))
        if cle in vues:
            continue
        vues.add(cle)
        offres.append(node)
    return offres


def _text(value: Any) -> str:
    """Texte lisible : les champs JSON-LD contiennent souvent des entites HTML."""
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("value") or "")
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if v)
    return html.unescape(str(value or ""))


def _location(node: dict[str, Any]) -> str:
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    parts: list[str] = []
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                val = addr.get(key)
                if val and str(val) not in parts:
                    parts.append(str(val))
        elif addr:
            parts.append(str(addr))
        if not parts and loc.get("name"):
            parts.append(str(loc["name"]))
    if str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        parts.append("Remote")
    return ", ".join(parts)


def _contract(node: dict[str, Any]) -> ContractType:
    raw = node.get("employmentType")
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        key = str(value or "").upper().replace("-", "_").replace(" ", "_")
        if key in _EMPLOYMENT_MAP:
            return _EMPLOYMENT_MAP[key]
    return ContractType.UNKNOWN


def _salary(node: dict[str, Any]) -> tuple[int | None, int | None]:
    """Lit baseSalary si l'unite est journaliere (DAY/DAILY)."""
    base = node.get("baseSalary")
    if not isinstance(base, dict):
        return None, None
    value = base.get("value")
    if not isinstance(value, dict):
        return None, None
    unit = str(value.get("unitText", "")).upper()
    if unit not in ("DAY", "DAILY"):
        return None, None

    def to_int(v: Any) -> int | None:
        try:
            n = int(float(v))
            return n if 100 <= n <= 5000 else None
        except (TypeError, ValueError):
            return None

    lo = to_int(value.get("minValue")) or to_int(value.get("value"))
    hi = to_int(value.get("maxValue")) or lo
    return lo, hi


def offer_from_jsonld(node: dict[str, Any], *, source: str, url: str) -> JobOffer:
    """Convertit un noeud JobPosting en JobOffer (non normalise)."""
    description_html = str(node.get("description") or "")
    lo, hi = _salary(node)
    return JobOffer(
        source=source,
        source_id=str(node.get("identifier") or node.get("@id") or "")[:120],
        url=str(node.get("url") or url),
        title=_text(node.get("title")),
        company=_text(node.get("hiringOrganization")),
        description=strip_html(description_html),
        raw_html=description_html,
        location=_location(node),
        contract=_contract(node),
        daily_rate_min=lo,
        daily_rate_max=hi,
        published_at=parse_datetime(node.get("datePosted")),
    )
