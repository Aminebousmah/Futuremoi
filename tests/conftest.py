"""Fixtures partagees."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from freelance_radar.config import Config, Profile
from freelance_radar.models import ContractType, JobOffer, RemotePolicy


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config.model_validate({
        "search": {
            "keywords_any": ["data", "analytics", "bi"],
            "exclude_any": ["stage", "alternance", "data entry"],
            "min_skills_without_title_match": 2,
        },
        "filters": {
            "contracts": ["freelance"],
            "max_age_days": 30,
            "min_daily_rate": 400,
            "locations": ["paris", "remote", "france"],
            "locations_exclude": ["brazil", "latam"],
            "remote_only": False,
        },
        "storage": {"database": str(tmp_path / "test.db"), "cache_dir": str(tmp_path / "cache")},
        "application": {"output_dir": str(tmp_path / "apps"), "use_llm": False},
    })


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate({
        "identity": {"full_name": "Ada Test", "title": "Data Engineer freelance",
                     "email": "ada@example.com"},
        "positioning": {"pitch": "8 ans sur des plateformes data.", "years_experience": 8},
        "skills": {"expert": ["Python", "SQL", "dbt"], "advanced": ["Snowflake", "Airflow"],
                   "familiar": ["AWS"]},
        "constraints": {"daily_rate_target": 650, "daily_rate_floor": 500,
                        "remote": "hybrid", "preferred_duration_months": 6,
                        "available_from": "2020-01-01"},
        "references": [{"client": "Retailer", "role": "Data Engineer", "period": "2024",
                        "stack": ["dbt", "Snowflake"], "achievement": "Refonte du DWH."}],
        "documents": {"cv_pdf": "assets/CV.pdf"},
        "cv": {
            "competences": {
                "Visualisation & BI": ["Power BI", "Tableau", "Excel", "Qlik"],
                "Langages & requetage": ["Python", "SQL", "R", "DAX"],
                "Ingenierie & orchestration": ["dbt", "Airflow", "Spark", "Kafka"],
                "Data science & ML": ["Scikit-learn", "MLflow", "XGBoost", "MLOps"],
                "Entrepots & bases": ["Snowflake", "BigQuery", "PostgreSQL"],
                "Analyse & pilotage": ["Analyse metier", "KPI", "Cadrage du besoin"],
            },
            # Libelles accentues : ce sont eux que la table d'alias reconnait.
            "transverses": ["Communication", "Autonomie", "Rigueur",
                            "Travail en équipe", "Pédagogie et formation"],
            "rubriques_fixes": [
                {"label": "Langues", "outils": ["Francais", "Anglais"]},
            ],
        },
    })


def make_offer(**kwargs) -> JobOffer:
    """Offre par defaut : freelance, Paris, recente, dans les clous des filtres."""
    defaults = dict(
        source="test",
        url="https://example.com/offre",
        title="Data Engineer Senior",
        company="ACME",
        description="Mission freelance de 6 mois. Stack Python, SQL, dbt, Snowflake, Airflow.",
        location="Paris, France",
        remote=RemotePolicy.HYBRID,
        contract=ContractType.FREELANCE,
        daily_rate_min=600,
        daily_rate_max=650,
        duration_months=6.0,
        skills=["Python", "SQL", "dbt"],
        published_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    defaults.update(kwargs)
    return JobOffer(**defaults)


@pytest.fixture
def offer() -> JobOffer:
    return make_offer()
