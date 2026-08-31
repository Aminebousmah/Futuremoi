"""Enrichissement : extraction des competences et du contexte metier.

L'extraction repose sur une taxonomie explicite plutot que sur un modele : c'est
deterministe, auditable et suffisant pour du vocabulaire technique ou les termes
sont stables. Chaque entree est `canonique -> variantes reconnues`.
"""

from __future__ import annotations

import re

from ..models import ContractType, JobOffer
from . import freelance
from .normalize import normalize_key

SKILL_TAXONOMY: dict[str, list[str]] = {
    # --- Langages & requetage ---
    "Python": ["python", "pandas", "polars", "pyspark"],
    "SQL": ["sql", "t-sql", "pl/sql", "requetes sql", "requetage"],
    "R": ["langage r", " r "],
    "Scala": ["scala"],
    "Java": ["java"],
    "Bash": ["bash", "shell scripting"],
    # --- Entrepots & bases ---
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery", "big query"],
    "Redshift": ["redshift"],
    "Databricks": ["databricks", "delta lake", "lakehouse"],
    "PostgreSQL": ["postgres", "postgresql"],
    "MySQL": ["mysql", "mariadb"],
    "MongoDB": ["mongodb", "mongo"],
    "ClickHouse": ["clickhouse"],
    "Oracle": ["oracle", "exadata"],
    "SQL Server": ["sql server", "ssms", "ssis", "ssas"],
    # --- Transformation & orchestration ---
    "dbt": ["dbt", "data build tool"],
    "Airflow": ["airflow", "mwaa", "composer"],
    "Dagster": ["dagster"],
    "Prefect": ["prefect"],
    "Talend": ["talend"],
    "Dataiku": ["dataiku", "dataiku dss", "dss"],
    "Informatica": ["informatica"],
    "Azure Data Factory": ["data factory", "adf"],
    "Fivetran": ["fivetran"],
    "Airbyte": ["airbyte"],
    # --- Streaming & big data ---
    "Spark": ["spark", "pyspark", "databricks runtime"],
    "Kafka": ["kafka", "confluent"],
    "Flink": ["flink"],
    "Hadoop": ["hadoop", "hdfs", "hive"],
    # --- Cloud & infra ---
    "AWS": ["aws", "amazon web services", "s3", "glue", "lambda", "athena"],
    "GCP": ["gcp", "google cloud"],
    "Azure": ["azure", "synapse", "microsoft fabric"],
    "Terraform": ["terraform", "iac"],
    "Docker": ["docker", "conteneur"],
    "Kubernetes": ["kubernetes", "k8s", "helm"],
    "CI/CD": ["ci/cd", "gitlab ci", "github actions", "jenkins"],
    # --- BI & dataviz ---
    "Power BI": ["power bi", "powerbi", "dax"],
    "Tableau": ["tableau software", "tableau desktop"],
    "Looker": ["looker", "lookml"],
    "Qlik": ["qlik", "qlikview", "qliksense"],
    "Metabase": ["metabase"],
    "Excel": ["excel", "power query", "power pivot", "vba", "tableur"],
    "SAP BO": ["sap bi4", "sap bo", "businessobjects", "business objects",
               "webi", "web intelligence"],
    "Superset": ["superset"],
    "Streamlit": ["streamlit", "dash", "gradio"],
    # --- Data science & ML ---
    "Machine Learning": ["machine learning", "apprentissage automatique", "scikit-learn",
                         "sklearn", "xgboost"],
    "Deep Learning": ["deep learning", "pytorch", "tensorflow", "keras"],
    "MLOps": ["mlops", "mlflow", "kubeflow", "sagemaker", "vertex ai"],
    "NLP": ["nlp", "traitement du langage", "llm", "genai", "rag"],
    "Statistiques": ["statistique", "econometrie", "ab test", "a/b test"],
    # --- Gouvernance & qualite ---
    "Data Governance": ["gouvernance", "data governance", "rgpd", "gdpr",
                        "data catalog", "collibra", "datahub", "mdm",
                        "master data management", "donnees de reference",
                        "referentiel de donnees", "data steward"],
    "Data Quality": ["data quality", "qualite des donnees", "great expectations",
                     "soda", "monte carlo", "fiabilite des donnees",
                     "controle qualite des donnees"],
    "Data Modeling": ["modelisation", "data modeling", "kimball", "data vault",
                      "star schema", "modele en etoile", "modele de donnees",
                      "modeles de donnees", "data modeler", "merise",
                      "architecture de donnees", "data architect"],
    # --- Concepts metier (tres presents dans les annonces francaises) ---
    "ETL": ["etl", "elt", "integration de donnees", "flux de donnees",
            "ingestion", "alimentation"],
    "Data Warehouse": ["datawarehouse", "data warehouse", "entrepot de donnees",
                       "datamart", "data mart", "decisionnel", "sid"],
    "Data Lake": ["data lake", "datalake", "lac de donnees"],
    "Reporting": ["reporting", "tableau de bord", "dashboard", "kpi",
                  "self-service bi", "pilotage"],
    "Data Platform": ["data platform", "plateforme data", "socle data",
                      "data mesh", "data product"],
    # --- Outils encore courants chez les grands comptes FR ---
    "SAS": ["logiciel sas", "sas base", "sas eg"],
    "Alteryx": ["alteryx"],
    "MicroStrategy": ["microstrategy"],
    "Cloudera": ["cloudera", "hortonworks"],
    "Starburst": ["starburst", "trino", "presto"],
    "SAP BW": ["sap bw", "sap analytics", "sap hana"],
    # --- Methodes ---
    "Agile": ["agile", "scrum", "kanban", "safe"],
}

# Familles de metiers, utilisees pour l'angle de la lettre de motivation
ROLE_FAMILIES: dict[str, list[str]] = {
    "data_engineer": ["data engineer", "ingenieur data", "ingenieur donnees",
                      "data platform", "etl developer", "pipeline"],
    "analytics_engineer": ["analytics engineer", "dbt", "modelisation analytique"],
    "data_analyst": ["data analyst", "analyste donnees", "business analyst data",
                     "analyste decisionnel"],
    "bi_engineer": ["business intelligence", "consultant bi", "developpeur bi",
                    "power bi", "tableau", "qlik"],
    "data_scientist": ["data scientist", "machine learning engineer", "ml engineer",
                       "statisticien"],
    "data_architect": ["data architect", "architecte data", "architecte donnees"],
    "data_manager": ["data manager", "head of data", "chef de projet data",
                     "product owner data", "data steward"],
}

# Ce qu'un intitule de poste implique, meme quand l'annonce ne detaille rien.
#
# Raison d'etre : beaucoup de sources tronquent leur texte -- l'API Adzuna rend
# 500 caracteres. Sur une annonce "Data engineer" ainsi coupee, aucun outil
# n'est cite, toutes les competences tombent a zero, et la composition du CV
# se rabat sur le haut de l'inventaire. Le titre, lui, dit clairement le
# metier : autant s'en servir.
#
# Les intitules reprennent ceux de `profile.cv.competences`. Un nom qui n'y
# figure pas ne declenche simplement rien : la table se degrade, elle ne casse
# pas. `test_cv.py` verifie qu'elle reste majoritairement branchee.
COMPETENCES_ATTENDUES: dict[str, list[str]] = {
    "data_engineer": [
        "SQL", "Python", "dbt", "Airflow", "Spark / PySpark", "Snowflake",
        "BigQuery", "Databricks", "ETL / ELT", "Azure Data Factory", "CI/CD",
    ],
    "analytics_engineer": [
        "dbt", "SQL", "Modélisation dimensionnelle", "Modèle en étoile (Kimball)",
        "Snowflake", "BigQuery", "Qualité des données", "Git",
    ],
    "data_analyst": [
        "SQL", "Power BI", "Excel (Power Query, TCD, VBA)", "Python", "Tableau",
        "Analyse métier", "KPI et tableaux de bord", "Self-service BI",
    ],
    "bi_engineer": [
        "Power BI", "DAX", "Power Query (langage M)", "SQL", "Tableau",
        "Modèle en étoile (Kimball)", "Modèle sémantique",
        "SAP BusinessObjects (BI4)",
    ],
    "data_scientist": [
        "Python", "Scikit-learn", "Statistiques appliquées", "SQL", "XGBoost",
        "Séries temporelles", "MLflow", "MLOps", "Clustering",
        "Modèles de scoring",
    ],
    "data_architect": [
        "Modèle en étoile (Kimball)", "Modélisation dimensionnelle", "Data Vault",
        "Gouvernance des données", "Snowflake", "Azure", "Data lineage",
        "Dictionnaire de données",
    ],
    "data_manager": [
        "Gouvernance des données", "Qualité des données", "Analyse métier",
        "MDM / données de référence", "Cadrage du besoin", "Agile / Scrum",
        "Gestion de projet", "Documentation technique",
    ],
}


def competences_attendues(famille: str) -> list[str]:
    """Competences qu'un intitule de poste laisse attendre, sans les citer."""
    return list(COMPETENCES_ATTENDUES.get(famille, []))


# Competences qui signent reellement un poste data. Le reste de la taxonomie
# (Python, Java, Docker, CI/CD, AWS, SQL...) est partage avec le developpement
# logiciel : trouver trois de ces outils dans une annonce ne prouve pas qu'elle
# soit data. Cette distinction est ce qui empeche un "Senior Software Engineer"
# ou un "QA Engineer" de remonter dans un radar Data.
CORE_DATA_SKILLS = frozenset({
    # Entrepots et plateformes analytiques
    "Snowflake", "BigQuery", "Redshift", "Databricks", "ClickHouse",
    # Transformation et orchestration
    "dbt", "Airflow", "Dagster", "Prefect", "Talend", "Informatica",
    "Azure Data Factory", "Fivetran", "Airbyte", "Dataiku", "Alteryx",
    # Big data
    "Spark", "Hadoop", "Flink", "Cloudera", "Starburst",
    # BI et restitution
    "Power BI", "Tableau", "Looker", "Qlik", "Metabase", "Superset",
    "MicroStrategy", "SAP BO", "SAP BW", "SAS",
    # Science des donnees
    "Machine Learning", "Deep Learning", "MLOps", "NLP", "Statistiques", "R",
    # Concepts metier
    "ETL", "Data Warehouse", "Data Lake", "Reporting", "Data Platform",
    "Data Governance", "Data Quality", "Data Modeling",
})


def count_core_skills(skills: list[str]) -> int:
    """Nombre de competences specifiquement data parmi celles detectees."""
    return sum(1 for s in skills if s in CORE_DATA_SKILLS)


def _build_patterns(taxonomy: dict[str, list[str]]) -> list[tuple[str, re.Pattern[str]]]:
    """Precompile un motif par competence (alternance de ses variantes)."""
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for canonical, variants in taxonomy.items():
        alts = sorted({normalize_key(v) for v in [canonical, *variants] if v.strip()},
                      key=len, reverse=True)
        parts = []
        for alt in alts:
            escaped = re.escape(alt.strip())
            # Frontieres de mot uniquement si l'extremite est alphanumerique
            left = r"\b" if alt[:1].isalnum() else ""
            right = r"\b" if alt[-1:].isalnum() else ""
            parts.append(f"{left}{escaped}{right}")
        compiled.append((canonical, re.compile("|".join(parts), re.IGNORECASE)))
    return compiled


_SKILL_PATTERNS = _build_patterns(SKILL_TAXONOMY)
_ROLE_PATTERNS = _build_patterns(ROLE_FAMILIES)


def extract_skills(text: str) -> list[str]:
    """Rend les competences canoniques citees dans le texte."""
    if not text:
        return []
    hay = normalize_key(text)
    return [canonical for canonical, rx in _SKILL_PATTERNS if rx.search(hay)]


def detect_role_family(title: str, description: str = "") -> str:
    """Famille de metier dominante. Le titre pese plus que la description."""
    for source, weight_first in ((title, True), (description, False)):
        hay = normalize_key(source)
        for family, rx in _ROLE_PATTERNS:
            if rx.search(hay):
                return family
        if weight_first and not description:
            break
    return "data_generic"


# Contrats qu'un signal freelance peut requalifier. Un CDI declare par la
# source ne l'est jamais : c'est une information positive, pas une absence.
_CONTRATS_PROMOUVABLES = frozenset({ContractType.UNKNOWN, ContractType.CDD})


def enrich_offer(offer: JobOffer) -> JobOffer:
    """Remplace les tags bruts de la source par des competences canoniques.

    Les job boards utilisent des vocabulaires incomparables entre eux ("exec",
    "data entry", "senior"). On les reinjecte dans le texte analyse, puis on ne
    conserve que la taxonomie : les competences deviennent comparables d'une
    source a l'autre, ce dont dependent le scoring et les filtres.
    """
    blob = f"{offer.title}\n{offer.description}\n{' '.join(offer.skills)}"
    offer.skills = extract_skills(blob)

    # Ce que la source n'a pas su declarer : TJM et nature freelance.
    signals = freelance.detect(offer.title, offer.description)
    if offer.daily_rate_min is None and offer.daily_rate_max is None:
        offer.daily_rate_min = signals.rate_min
        offer.daily_rate_max = signals.rate_max
    if signals.freelance and offer.contract in _CONTRATS_PROMOUVABLES:
        offer.contract = ContractType.FREELANCE
        offer.freelance_marker = signals.marker

    return offer
