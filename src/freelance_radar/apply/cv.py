"""Adaptation du CV a une offre : ordre des competences et paragraphe de profil.

Ce module ne touche a rien : il calcule QUOI changer et le rend sous une forme
directement applicable a un CV Canva (voir `docs/cv-canva.md`). Le CV d'origine
n'est jamais modifie — l'application se fait sur une copie, une par offre.

Deux transformations, et deux seulement :
  * l'ORDRE des rubriques de competences, pour que celles que l'offre demande
    apparaissent en premier ;
  * le paragraphe de PROFIL, reecrit avec le vocabulaire de la mission.

Les experiences, les chiffres et les dates ne sont jamais touches : ce sont des
faits, les reordonner ou les reformuler reviendrait a les maquiller.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..config import Profile
from ..models import JobOffer
from ..pipeline.enrich import detect_role_family
from ..pipeline.normalize import normalize_key


@dataclass
class Rubrique:
    """Une ligne de la section Competences : un intitule et sa liste d'outils."""

    label: str
    outils: list[str]
    epinglee: bool = False        # non dependante de l'offre : ne bouge pas
    score: float = 0.0            # pertinence calculee pour l'offre

    def texte(self) -> str:
        return " · ".join(self.outils)


@dataclass
class ProfilAdapte:
    """Le paragraphe de profil, decoupe comme dans le CV (deux segments en gras)."""

    initiale: str      # premiere lettre, isolee dans la maquette
    tete: str          # suite de l'accroche, en gras
    liaison: str       # texte courant
    accent: str        # segment mis en gras
    fin: str           # texte courant

    def texte(self) -> str:
        return f"{self.initiale}{self.tete}{self.liaison}{self.accent}{self.fin}"


@dataclass
class AdaptationCV:
    rubriques: list[Rubrique] = field(default_factory=list)
    profil: ProfilAdapte | None = None
    competences_mises_en_avant: list[str] = field(default_factory=list)

    def resume(self) -> str:
        ordre = " > ".join(r.label for r in self.rubriques if not r.epinglee)
        return f"Ordre des rubriques : {ordre}"


# Rubriques du CV, dans leur ordre d'origine. `epinglee` marque celles qui ne
# dependent pas de l'offre : les deplacer n'apporterait rien et deroutrait la
# lecture. Les autres sont reordonnees par pertinence.
RUBRIQUES_CV: list[Rubrique] = [
    Rubrique("Compétences transverses",
             ["Gestion de projet agile (Git, Jira, Notion)", "Communication", "Analyse métier"],
             epinglee=True),
    Rubrique("Langues", ["Français (natif)", "Anglais (courant)"], epinglee=True),
    Rubrique("Data & Programmation", ["Python", "SQL", "MongoDB", "R"]),
    Rubrique("Machine Learning & IA",
             ["Scikit-learn", "XGBoost", "TensorFlow", "MLflow", "LangChain", "Vector DB"]),
    Rubrique("Data Engineering & Big Data",
             ["Spark", "Airflow", "Kafka", "Databricks", "Snowflake", "dbt", "Dataiku"]),
    Rubrique("Cloud & DevOps", ["AWS", "Azure", "Docker", "CI/CD", "Linux"]),
    Rubrique("Data Visualisation & BI",
             ["Tableau", "Power BI", "Excel (Power Query, VBA)"]),
]

# Angle du paragraphe de profil selon la famille de poste detectee.
_ANGLES = {
    "data_analyst": ("l'analyse de données au service de la décision",
                     "l'exploration, la fiabilisation et la restitution des données"),
    "bi_engineer": ("la Business Intelligence et le pilotage par la donnée",
                    "la modélisation décisionnelle et des tableaux de bord"),
    "analytics_engineer": ("l'ingénierie analytique",
                           "la modélisation des données et la qualité des livrables"),
    "data_engineer": ("l'industrialisation des chaînes de données",
                      "l'ingestion, la transformation et l'orchestration des flux"),
    "data_scientist": ("la data science appliquée au métier",
                       "la modélisation statistique et sa mise en production"),
    "data_architect": ("l'architecture des plateformes de données",
                       "la conception de modèles de données partagés"),
    "data_manager": ("le pilotage de projets data",
                     "le cadrage du besoin métier et la conduite de chantiers data"),
    "data_generic": ("la donnée au service de la décision",
                     "la construction de chaînes de données exploitables"),
}


def _pertinence(rubrique: Rubrique, demandees: set[str]) -> float:
    """Part des outils de la rubrique que l'offre mentionne.

    On combine la proportion et le nombre absolu : une rubrique de trois outils
    dont deux sont demandes vaut mieux qu'une rubrique de sept dont deux le sont,
    mais deux correspondances valent toujours mieux qu'une seule.
    """
    if not rubrique.outils:
        return 0.0
    touches = sum(1 for o in rubrique.outils
                  if any(normalize_key(o).startswith(normalize_key(d))
                         or normalize_key(d) in normalize_key(o) for d in demandees))
    if not touches:
        return 0.0
    return touches + (touches / len(rubrique.outils))


def ordonner_rubriques(offer: JobOffer, profile: Profile) -> list[Rubrique]:
    """Rubriques epinglees a leur place, les autres triees par pertinence."""
    demandees = {normalize_key(s) for s in offer.skills}
    demandees |= {normalize_key(m) for m in (offer.score_detail.get("_matched_skills") or [])}

    # Copies : RUBRIQUES_CV est partage par tout le processus. Muter ses
    # elements ferait fuiter le score d'une offre sur la suivante.
    rubriques = [replace(r) for r in RUBRIQUES_CV]
    mobiles = [r for r in rubriques if not r.epinglee]
    for r in mobiles:
        r.score = _pertinence(r, demandees)
    # Tri stable : a pertinence egale, l'ordre d'origine du CV est conserve.
    mobiles = sorted(mobiles, key=lambda r: -r.score)

    resultat: list[Rubrique] = []
    file_mobiles = iter(mobiles)
    for r in rubriques:
        resultat.append(r if r.epinglee else next(file_mobiles))
    return resultat


def reecrire_profil(offer: JobOffer, profile: Profile) -> ProfilAdapte:
    """Reecrit le paragraphe de profil avec le vocabulaire de la mission.

    L'accroche reste le titre reel du candidat : c'est un fait, pas un
    positionnement negociable. Seuls le domaine vise et les competences mises
    en avant changent d'une offre a l'autre.
    """
    famille = detect_role_family(offer.title, offer.description)
    domaine, geste = _ANGLES.get(famille, _ANGLES["data_generic"])

    communes = offer.score_detail.get("_matched_skills") or []
    if not communes:
        communes = [s for s in offer.skills if s in profile.all_skills]
    mises_en_avant = communes[:3] or profile.skills.get("expert", [])[:3]

    if len(mises_en_avant) >= 2:
        accent = ", ".join(mises_en_avant[:-1]) + f" et {mises_en_avant[-1]}"
    else:
        accent = mises_en_avant[0] if mises_en_avant else "la donnée décisionnelle"

    annees = profile.positioning.get("years_experience", 3)
    tete_complete = "Ingénieur Data & IA"
    return ProfilAdapte(
        initiale=tete_complete[0],
        tete=tete_complete[1:],
        liaison=f", {annees} ans d'analytics appliquée à la finance, tourné vers {domaine}. "
                f"Sur cette mission, j'interviens avec ",
        accent=accent,
        fin=f", au service de {geste}. J'apporte rigueur, autonomie et sens du métier.",
    )


def adapter_cv(offer: JobOffer, profile: Profile) -> AdaptationCV:
    rubriques = ordonner_rubriques(offer, profile)
    profil = reecrire_profil(offer, profile)
    return AdaptationCV(
        rubriques=rubriques,
        profil=profil,
        competences_mises_en_avant=[r.label for r in rubriques if not r.epinglee and r.score > 0],
    )


# --------------------------------------------------------------------------- #
#  Serialisation vers la maquette Canva
# --------------------------------------------------------------------------- #
def zones_profil(profil: ProfilAdapte) -> list[str]:
    """Les 5 zones de texte du paragraphe PROFIL, dans l'ordre de la maquette.

    Le decoupage est impose par le CV : zone 0 = initiale, zones 1 et 3 en gras.
    En conservant le nombre et le role de chaque zone, la mise en forme survit
    a la reecriture.
    """
    return [profil.initiale, profil.tete, profil.liaison, profil.accent, profil.fin]


def zones_competences(rubriques: list[Rubrique]) -> list[str]:
    """Les 14 zones du bloc COMPETENCES : 7 paires (intitule, outils).

    Les zones alternent intitule et valeurs, chacune avec son style. On permute
    donc des PAIRES, jamais des zones isolees : un intitule reste dans une zone
    d'intitule, et le gras est preserve. Les sauts de ligne sont normalises en
    tete d'intitule, la maquette d'origine les placant de facon irreguliere.
    """
    zones: list[str] = []
    for i, r in enumerate(rubriques):
        prefixe = "" if i == 0 else "\n"
        zones.append(f"{prefixe}{r.label} : ")
        zones.append(r.texte())
    return zones


def plan_canva(offer: JobOffer, profile: Profile,
               adaptation: AdaptationCV) -> dict[str, object] | None:
    """Plan applicable a Canva, ou None si aucun CV Canva n'est renseigne."""
    carte = (profile.documents or {}).get("canva_cv") or {}
    if not carte.get("design_id"):
        return None
    page = carte.get("page", "")

    def locator(element: str) -> str:
        return f"{page}-{element}" if page else element

    return {
        "design_source": carte["design_id"],
        "titre_copie": f"CV — {offer.company or offer.source} — {offer.title}"[:80],
        "edits": [
            {"element_id": locator(carte.get("element_profil", "")),
             "role": "paragraphe profil",
             "zones": zones_profil(adaptation.profil)},
            {"element_id": locator(carte.get("element_competences", "")),
             "role": "bloc competences",
             "zones": zones_competences(adaptation.rubriques)},
        ],
    }
