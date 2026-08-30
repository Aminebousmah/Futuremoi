"""Exports du contenu de la base : HTML lisible, CSV et JSON pour la suite."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from ..models import JobOffer
from ..storage import Database

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def export_html(offers: list[JobOffer], db: Database, cfg: Config, out: Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(default_for_string=True, default=True),
    )
    counts = db.counts_by_status()
    scores = [o.score for o in offers] or [0.0]
    html = env.get_template("report.html.j2").render(
        offers=offers,
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
        threshold=cfg.scoring.apply_threshold,
        stats={
            "total": sum(counts.values()),
            "new": counts.get("new", 0),
            "drafted": counts.get("drafted", 0),
            "sent": counts.get("sent", 0),
            "avg_score": round(sum(scores) / len(scores)),
        },
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def export_csv(offers: list[JobOffer], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "score", "title", "company", "source", "location", "remote",
              "contract", "daily_rate_min", "daily_rate_max", "duration_months",
              "published_at", "status", "skills", "url"]
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for o in offers:
            writer.writerow({
                "id": o.id, "score": o.score, "title": o.title, "company": o.company,
                "source": o.source, "location": o.location, "remote": o.remote.value,
                "contract": o.contract.value, "daily_rate_min": o.daily_rate_min,
                "daily_rate_max": o.daily_rate_max, "duration_months": o.duration_months,
                "published_at": o.published_at.isoformat() if o.published_at else "",
                "status": o.status.value, "skills": ", ".join(o.skills), "url": o.url,
            })
    return out


def export_json(offers: list[JobOffer], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = [o.model_dump(mode="json") for o in offers]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
