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


@dataclass
class Besoin:
    """Un besoin exprimable par une offre, et les outils qui y repondent."""

    motifs: str
    label: str
    domaines: list[str]
    # Besoins que celui-ci entraine mecaniquement : une mission BI suppose un
    # modele semantique, meme si l'annonce ne le dit pas. C'est ainsi qu'on
    # repond a la demande sans se limiter a ses mots.
    implique: list[str] = field(default_factory=list)


# Ce qu'une offre demande, traduit en domaines d'outils.
#
# Le principe : une annonce parle besoin ("modèles sémantiques complexes",
# "fusion de rapports"), pas outillage. Reprendre ses mots au mot ne prouve
# rien ; montrer les OUTILS qui servent ce besoin, si.
#
# `motifs`  : ce qu'on cherche dans l'annonce (sans accents, minuscules)
# `label`   : l'intitulé de rubrique qui en découlera dans le CV
# `domaines`: les domaines de l'inventaire à puiser, dans l'ordre
BESOINS: list[Besoin] = [
    Besoin(r"modele\s+semantique|semantique|modelisation|modele\s+de\s+donnees|"
           r"data\s+model|star\s+schema|schema\s+en\s+etoile|dax|mesures|"
           r"optimisation|performance|refonte",
           "Modélisation & sémantique", ["modelisation"]),

    Besoin(r"dashboard|tableau\s+de\s+bord|dataviz|data\s+visualization|visualisation|"
           r"restitution|reporting|rapport|power\s+bi|tableau|qlik",
           "Data Visualisation & BI", ["dataviz"],
           # Un poste BI suppose toujours un modele derriere les visuels, et des
           # sources a preparer : on les propose sans que l'annonce les nomme.
           implique=["Modélisation & sémantique", "Préparation & transformation"]),

    Besoin(r"source|preparation|nettoyage|transformation|power\s+query|fusion|"
           r"consolidation|harmonisation|simplification",
           "Préparation & transformation", ["preparation"]),

    Besoin(r"qualite|fiabilite|controle|gouvernance|coherence|anomalie",
           "Qualité & fiabilité des données", ["qualite"]),

    Besoin(r"pipeline|etl|elt|ingestion|orchestration|flux|entrepot|"
           r"datawarehouse|lakehouse",
           "Ingénierie de données", ["pipelines"],
           implique=["Cloud & industrialisation"]),

    Besoin(r"cloud|azure|aws|gcp|devops|ci/cd|industrialisation|deploiement",
           "Cloud & industrialisation", ["cloud"]),

    Besoin(r"machine\s+learning|\bml\b|\bia\b|intelligence\s+artificielle|"
           r"predictif|scoring|nlp|llm|data\s+scien",
           "Machine Learning & IA", ["ml"],
           implique=["Data & Programmation"]),

    Besoin(r"metier|besoin|cadrage|pilotage|budget|decision|atelier|accompagnement|"
           r"controle\s+de\s+gestion",
           "Pilotage & relation métier", ["pilotage"]),

    Besoin(r"python|sql|\br\b|requet|script|developpement",
           "Data & Programmation", ["programmation"]),
]


# Rubriques ajoutées quand l'offre n'en demande pas assez : elles montrent
# l'étendue du profil sans s'éloigner du poste.
COMPLEMENTS: list[tuple[str, list[str]]] = [
    ("Data & Programmation", ["programmation"]),
    ("Data Engineering & Big Data", ["pipelines"]),
    ("Cloud & DevOps", ["cloud"]),
    ("Machine Learning & IA", ["ml"]),
]

# Le bloc COMPÉTENCES du CV tient 7 lignes. Deux sont fixes, il reste donc
# cinq rubriques composables : au-delà, la maquette déborde.
RUBRIQUES_MOBILES = 5


def _inventaire(profile: Profile) -> dict[str, list[str]]:
    return dict((profile.cv or {}).get("outils") or {})


def _rubriques_fixes(profile: Profile) -> list[Rubrique]:
    brutes = (profile.cv or {}).get("rubriques_fixes") or []
    return [Rubrique(label=r.get("label", ""), outils=list(r.get("outils") or []),
                     epinglee=True) for r in brutes if r.get("label")]


def besoins_detectes(offer: JobOffer) -> list[tuple[str, list[str], float]]:
    """Besoins exprimes par l'offre, du plus marque au moins marque.

    Le titre pese plus lourd que le corps : c'est lui qui dit le metier vise.
    """
    titre = normalize_key(offer.title)
    corps = normalize_key(offer.description)[:6000]
    par_label = {b.label: b for b in BESOINS}
    poids: dict[str, float] = {}

    for besoin in BESOINS:
        score = 0.0
        if re.search(besoin.motifs, titre):
            score += 3.0
        score += min(len(re.findall(besoin.motifs, corps)), 4) * 0.75
        if score:
            poids[besoin.label] = poids.get(besoin.label, 0.0) + score

    # Les besoins impliques heritent d'une fraction du poids de leur declencheur :
    # ils comptent, mais jamais plus que ce que l'offre demande explicitement.
    for label, score in list(poids.items()):
        for suivant in par_label[label].implique:
            poids[suivant] = max(poids.get(suivant, 0.0), score * 0.6)

    return sorted(((label, par_label[label].domaines, p) for label, p in poids.items()),
                  key=lambda x: -x[2])


def composer_rubriques(offer: JobOffer, profile: Profile) -> list[Rubrique]:
    """Compose les rubriques de competences a partir de ce que l'offre demande.

    On ne reordonne pas des rubriques figees : on en fabrique, en puisant dans
    l'inventaire du profil les outils qui repondent au besoin detecte. Un outil
    n'apparait qu'une fois, dans la rubrique la plus pertinente.
    """
    inventaire = _inventaire(profile)
    if not inventaire:                       # profil sans inventaire : rien a composer
        return _rubriques_fixes(profile)

    deja_cites: set[str] = set()
    composees: list[Rubrique] = []

    def ajouter(label: str, domaines: list[str], score: float) -> None:
        outils = []
        for domaine in domaines:
            for outil in inventaire.get(domaine, []):
                if outil not in deja_cites and outil not in outils:
                    outils.append(outil)
        if len(outils) < 2:                  # une rubrique d'un seul outil fait maigre
            return
        deja_cites.update(outils)
        composees.append(Rubrique(label=label, outils=outils[:7], score=score))

    for label, domaines, poids in besoins_detectes(offer):
        if len(composees) >= RUBRIQUES_MOBILES:
            break
        ajouter(label, domaines, poids)

    # Depasser un peu le cadre : on complete avec les forces adjacentes.
    for label, domaines in COMPLEMENTS:
        if len(composees) >= RUBRIQUES_MOBILES:
            break
        if any(r.label == label for r in composees):
            continue
        ajouter(label, domaines, 0.0)

    return _rubriques_fixes(profile) + composees


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
