"""Adaptation du CV a une offre : ordre des competences et paragraphe de profil.

Ce module ne touche a rien : il calcule QUOI changer et le rend sous une forme
directement applicable a un CV Canva (voir `docs/cv-canva.md`). Le CV d'origine
n'est jamais modifie — l'application se fait sur une copie, une par offre.

Deux transformations, et deux seulement :
  * la section COMPETENCES, entierement composee a partir de l'offre : ses
    rubriques sont nommees d'apres le besoin detecte et remplies avec les
    outils de l'inventaire du profil qui y repondent ;
  * le paragraphe de PROFIL, reecrit avec le vocabulaire de la mission.

Le principe de la composition : une annonce parle besoin ("modeles semantiques
complexes"), pas outillage. Reprendre ses mots ne prouve rien ; montrer les
outils qui servent ce besoin, si. Certains besoins en entrainent d'autres — une
mission BI suppose un modele semantique meme sans le nommer — ce qui permet de
repondre a la demande et de la deborder un peu.

Les experiences, les chiffres et les dates ne sont jamais touches : ce sont des
faits, les reordonner ou les reformuler reviendrait a les maquiller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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


# Le bloc COMPETENCES du CV tient 7 lignes : "Compétences transverses",
# "Langues", et cinq rubriques composées.
RUBRIQUES_MOBILES = 5
COMPETENCES_RETENUES = 12      # ce qu'on met en avant, pas tout ce qu'on sait
COMPETENCES_PAR_RUBRIQUE = 4   # au-dela, une rubrique ecrase les autres
MINIMUM_PAR_RUBRIQUE = 2       # une ligne d'un seul element fait pauvre
TRANSVERSES_RETENUES = 4

# Formulations alternatives d'une meme competence, cote annonce. On ne liste
# que celles qu'une recherche de sous-chaine ne trouverait pas seule.
ALIAS: dict[str, list[str]] = {
    "Power BI": ["powerbi", "power-bi"],
    "Excel (Power Query, TCD, VBA)": ["excel", "tableau croise"],
    "SAP BusinessObjects (BI4)": ["business objects", "businessobjects", "bo", "webi"],
    "Power Query (langage M)": ["power query", "langage m"],
    "Modèle en étoile (Kimball)": ["etoile", "kimball", "star schema", "dimensionnel"],
    "Modèle sémantique": ["semantique", "modele de donnees", "dataset"],
    "Mesures et KPI (DAX)": ["dax", "mesure", "kpi"],
    "Spark / PySpark": ["spark", "pyspark"],
    "ETL / ELT": ["etl", "elt"],
    # Sans alias, un intitule a slash n'est cherche que tel quel : une annonce
    # qui dit "Matplotlib" ne matcherait pas "Matplotlib / Seaborn".
    "Matplotlib / Seaborn": ["matplotlib", "seaborn"],
    "Zapier / Make": ["zapier", "make.com"],
    # "fabric" seul est ambigu (c'est aussi une bibliotheque Python).
    "Microsoft Fabric": ["microsoft fabric", "ms fabric"],
    # Vocabulaire finance / performance : l'annonce dit "controle de gestion"
    # ou "ecarts budgetaires" la ou l'inventaire dit "Analyse d'ecarts".
    "Analyse d'écarts": ["ecarts budgetaires", "analyse d'ecarts", "variance"],
    "Analyse de rentabilité": ["rentabilite", "profitabilite", "marge"],
    "Consolidation de données": ["consolidation"],
    "Business case / ROI": ["business case", "roi", "retour sur investissement"],
    "Pilotage de la performance": ["pilotage de la performance", "performance management"],
    "Prévision de la demande": ["prevision de la demande", "demand planning",
                                "demand forecasting"],
    "Revenue management": ["revenue management", "yield management"],
    # Les annonces nomment le secteur plus souvent que l'outil financier.
    # Une annonce dit "Banque / CIB", jamais "finance & controle de gestion" :
    # sans les mots du secteur, la rubrique restait a zero sur ses meilleures
    # cibles.
    "Finance & contrôle de gestion": ["controle de gestion", "finance", "financier",
                                      "financiere", "budgetaire", "banque", "bancaire",
                                      "cib", "asset management", "comptable"],
    "Secteur public": ["secteur public", "ministere", "collectivite", "administration"],
    "Hôtellerie & tourisme": ["hotellerie", "tourisme", "hospitality"],
    "RSE / ESG": ["rse", "esg", "extra-financier", "decarbonation"],
    "Dataiku": ["dataiku", "dataiku dss"],
    "Hadoop": ["hadoop", "hdfs"],
    "Lakehouse": ["lakehouse", "lake house"],
    "Change Data Capture (CDC)": ["cdc", "change data capture"],
    "LLM (GPT, Claude)": ["llm", "gpt", "genai", "ia generative"],
    "RAG (retrieval augmented generation)": ["rag", "retrieval augmented"],
    "Bases vectorielles": ["vector", "embedding"],
    "Traitement du langage (NLP)": ["nlp", "traitement du langage"],
    "API REST": ["api rest", "rest api", "api"],
    "MDM / données de référence": ["mdm", "master data", "donnees de reference"],
    "Gouvernance des données": ["gouvernance"],
    "Qualité des données": ["qualite des donnees", "data quality"],
    "Contrôles qualité automatisés": ["controle qualite", "fiabilisation"],
    "KPI et tableaux de bord": ["kpi", "tableau de bord", "dashboard"],
    "Agile / Scrum": ["agile", "scrum"],
    # Savoir-etre : ce qu'une annonce dit quand elle les reclame.
    "Vulgarisation auprès du métier": ["vulgaris", "pedagog", "metier", "utilisateur"],
    "Autonomie": ["autonom", "independan"],
    "Rigueur": ["rigueur", "rigoureux", "precision", "fiabilite"],
    "Esprit d'analyse": ["analyse", "analytique", "esprit critique"],
    "Esprit de synthèse": ["synthese", "restitution"],
    "Travail en équipe": ["equipe", "collaboratif", "collectif"],
    "Force de proposition": ["force de proposition", "proactif", "initiative"],
    "Pédagogie et formation": ["formation", "former", "accompagnement", "montee en competence"],
    "Animation d'ateliers": ["atelier", "workshop", "animation"],
    "Gestion des priorités": ["priorit", "delai", "multi-projet"],
    "Relation client": ["client", "interlocuteur", "stakeholder"],
    "Conduite du changement": ["conduite du changement", "adoption", "transformation"],
    "Résolution de problèmes": ["resolution", "probleme", "troubleshooting"],
    "Adaptabilité": ["adaptab", "polyvalen", "flexib"],
    "Curiosité": ["curios", "veille"],
    "Communication": ["communication", "communiquer", "presentation"],
    "CI/CD": ["ci/cd", "cicd", "integration continue"],
    "Séries temporelles": ["serie temporelle", "time series", "forecast"],
    "Tests A/B": ["ab test", "a/b test"],
    "Statistiques appliquées": ["statistique", "econometrie"],
    "Prévision / forecast": ["prevision", "forecast", "budget"],
    "Self-service BI": ["self service", "self-service"],
    "Azure Data Factory": ["data factory", "adf"],
    "Apache NiFi": ["nifi"],
    "Looker Studio": ["looker studio", "data studio"],
    "Amazon SageMaker": ["sagemaker"],
    "Vertex AI": ["vertex"],
}


def _competences(profile: Profile) -> dict[str, list[str]]:
    return dict((profile.cv or {}).get("competences") or {})


def _transverses(profile: Profile) -> list[str]:
    return list((profile.cv or {}).get("transverses") or [])


def _rubriques_fixes(profile: Profile) -> list[Rubrique]:
    brutes = (profile.cv or {}).get("rubriques_fixes") or []
    return [Rubrique(label=r.get("label", ""), outils=list(r.get("outils") or []),
                     epinglee=True) for r in brutes if r.get("label")]


def _motifs(competence: str) -> list[str]:
    """Toutes les facons dont une annonce peut nommer cette competence."""
    base = normalize_key(re.sub(r"\([^)]*\)", "", competence))
    return [m for m in [base, *ALIAS.get(competence, [])] if m]


def _pertinence(competence: str, titre: str, corps: str,
                demandees: set[str]) -> float:
    """A quel point l'offre reclame cette competence.

    Le titre pese lourd : c'est la que se dit le coeur du poste. Les
    competences reconnues par la taxonomie du pipeline comptent aussi, meme
    formulees autrement dans le texte.
    """
    score = 0.0
    for motif in _motifs(competence):
        borne = rf"\b{re.escape(motif)}\b" if len(motif) <= 4 else re.escape(motif)
        if re.search(borne, titre):
            score += 5.0
        occurrences = len(re.findall(borne, corps))
        if occurrences:
            score += 1.5 + min(occurrences, 4) * 0.5
        if motif in demandees:
            score += 3.0
    return score


def classer_competences(offer: JobOffer, profile: Profile) -> list[tuple[str, str, float]]:
    """Toutes les competences de l'inventaire, notees pour cette offre.

    Rend des triplets (categorie, competence, score), du plus pertinent au moins.
    """
    titre = normalize_key(offer.title)
    corps = normalize_key(offer.description)[:8000]
    demandees = {normalize_key(s) for s in offer.skills}
    demandees |= {normalize_key(s)
                  for s in ((offer.score_detail or {}).get("_matched_skills") or [])}

    classees = []
    for categorie, liste in _competences(profile).items():
        for rang, competence in enumerate(liste):
            score = _pertinence(competence, titre, corps, demandees)
            # A score egal, l'ordre de l'inventaire departage : il place en
            # tete les outils les plus courants du metier.
            classees.append((categorie, competence, score - rang * 0.01))
    return sorted(classees, key=lambda x: -x[2])


def _transverses_rubrique(offer: JobOffer, profile: Profile) -> list[Rubrique]:
    """Les savoir-etre, choisis eux aussi selon l'offre."""
    pool = _transverses(profile)
    if not pool:
        return []
    titre = normalize_key(offer.title)
    corps = normalize_key(offer.description)[:8000]
    classes = sorted(
        ((t, _pertinence(t, titre, corps, set()) - i * 0.01) for i, t in enumerate(pool)),
        key=lambda x: -x[1],
    )
    return [Rubrique(label="Compétences transverses",
                     outils=[t for t, _ in classes[:TRANSVERSES_RETENUES]],
                     epinglee=True)]


def composer_rubriques(offer: JobOffer, profile: Profile) -> list[Rubrique]:
    """Retient les competences les plus proches de l'offre et les regroupe.

    Deux temps. On selectionne d'abord ce que l'annonce reclame explicitement.
    Puis, si le compte n'y est pas, on complete avec les competences voisines —
    celles des memes categories — ce qui repond au besoin tout en montrant un
    peu plus que le strict minimum.
    """
    classees = classer_competences(offer, profile)
    if not classees:
        return _rubriques_fixes(profile)

    scores = {(c, k): s for c, k, s in classees}
    retenues: list[tuple[str, str]] = []
    par_categorie: dict[str, int] = {}

    def prendre(candidats) -> None:
        """Ajoute des competences en respectant le plafond par categorie.

        Sans ce plafond, les premieres categories ouvertes absorbent les douze
        places et le CV n'affiche plus que deux lignes gonflees, ce qui donne
        moins d'informations qu'une repartition equilibree.
        """
        for categorie, competence, _score in candidats:
            if len(retenues) >= COMPETENCES_RETENUES:
                return
            if (categorie, competence) in retenues:
                continue
            if par_categorie.get(categorie, 0) >= COMPETENCES_PAR_RUBRIQUE:
                continue
            retenues.append((categorie, competence))
            par_categorie[categorie] = par_categorie.get(categorie, 0) + 1

    # 1. ce que l'annonce reclame explicitement
    prendre([x for x in classees if x[2] > 0])
    # 2. le voisinage des categories deja ouvertes, pour completer sans devier
    ouvertes = set(par_categorie)
    prendre([x for x in classees if x[0] in ouvertes])
    # 3. a defaut, le haut de l'inventaire
    prendre(classees)

    # Regroupement : dans chaque categorie, la competence la plus demandee
    # apparait en premier.
    groupes: dict[str, list[str]] = {}
    for categorie, competence in retenues:
        groupes.setdefault(categorie, []).append(competence)

    rubriques = [
        Rubrique(label=cat,
                 outils=sorted(liste, key=lambda k: -scores.get((cat, k), 0.0)),
                 score=max(scores.get((cat, k), 0.0) for k in liste))
        for cat, liste in groupes.items()
    ]
    rubriques.sort(key=lambda r: -r.score)

    # On ne replie jamais une categorie dans une autre : verser "dbt" sous
    # "Entrepots & bases de donnees" le rangerait sous un intitule qui ment.
    gardees = rubriques[:RUBRIQUES_MOBILES]

    def completer(rubrique: Rubrique, jusqu_a: int) -> None:
        """Etoffe une rubrique avec les competences de sa propre categorie."""
        for categorie, competence, _ in classees:
            if len(rubrique.outils) >= jusqu_a:
                return
            if categorie == rubrique.label and competence not in rubrique.outils:
                rubrique.outils.append(competence)

    # Une ligne d'un seul element fait pauvre : on la complete, et on ne
    # l'ecarte que si sa categorie n'a rien d'autre a offrir.
    for rubrique in gardees:
        if len(rubrique.outils) < MINIMUM_PAR_RUBRIQUE:
            completer(rubrique, MINIMUM_PAR_RUBRIQUE)
    gardees = [r for r in gardees if len(r.outils) >= MINIMUM_PAR_RUBRIQUE]

    # Les places restantes vont aux rubriques les plus pertinentes.
    for rubrique in gardees:
        restantes = COMPETENCES_RETENUES - sum(len(r.outils) for r in gardees)
        if restantes <= 0:
            break
        completer(rubrique, min(COMPETENCES_PAR_RUBRIQUE,
                                len(rubrique.outils) + restantes))

    return [*_transverses_rubrique(offer, profile), *_rubriques_fixes(profile), *gardees]


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
    rubriques = composer_rubriques(offer, profile)
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
